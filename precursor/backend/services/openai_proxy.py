"""OpenAI-compatible relay to the active LLM provider.

Lets any OpenAI client — VS Code's Custom Endpoint models, Open WebUI,
Continue, the ``openai`` SDK — use the models of Precursor's active provider by
pointing it at ``/api/openai/v1``. Precursor adds nothing to the conversation:
the client's messages and tools go to the provider as-is and the client runs
its own tool loop, so the relay is a pure translation between the OpenAI wire
format and the provider layer (which already hides chat-completions vs
Responses routing).

The endpoint is opt-in (``openai_proxy_enabled``), gated by a generated bearer
key, and only relays to providers that opt in via ``ProviderSpec.openai_proxy``.
The key is deliberately recoverable from Settings: Precursor is a single-user
app on a trusted host, and a key the user can't read back again is a key they
end up rotating every time they configure another client.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import AppSetting
from precursor.backend.services.app_settings import (
    resolve_llm_provider,
    resolve_llm_provider_config,
    resolve_openai_proxy_enabled,
    resolve_openai_proxy_key,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    LLMModel,
    LLMProvider,
    ProviderEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.llm.mock import MockProvider
from precursor.backend.services.llm.registry import PROVIDERS
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

BASE_PATH = "/api/openai/v1"
USAGE_SOURCE = "openai-endpoint"
KEY_SETTING = "openai_proxy_key"
# OpenAI-looking prefix: a few clients sanity-check that a key starts with it.
KEY_PREFIX = "sk-precursor-"

# Chat-completions parameters relayed to the provider. Everything else a client
# sends (``user``, ``metadata``, ``store``, ``logprobs``…) is dropped: those are
# account-side features of api.openai.com, not controls of the reply.
REQUEST_OPTIONS = (
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "stop",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "response_format",
    "tool_choice",
    "parallel_tool_calls",
)
# Meaningless — and rejected by OpenAI — when the request carries no tools.
_TOOL_OPTIONS = frozenset({"tool_choice", "parallel_tool_calls"})

_ROLES = {
    "system": "system",
    # The o-series renamed the system role; providers here all accept "system".
    "developer": "system",
    "user": "user",
    "assistant": "assistant",
    "tool": "tool",
}
_FINISH_REASONS = frozenset({"stop", "length", "content_filter"})


class ProxyError(Exception):
    """A request the endpoint answers with an OpenAI-shaped error body."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        code: str | None = None,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.type = "invalid_request_error" if status < 500 else "api_error"
        self.code = code
        self.param = param

    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.type,
                "param": self.param,
                "code": self.code,
            }
        }


# -- Availability and key ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProxyAvailability:
    available: bool
    reason: str | None = None


def supported_provider_labels() -> list[str]:
    return [spec.label for spec in PROVIDERS.values() if spec.openai_proxy]


async def proxy_availability(session: AsyncSession) -> ProxyAvailability:
    """Whether the active provider can back the endpoint right now.

    A provider that isn't configured makes ``get_llm_provider`` fall back to the
    mock, which would answer every client with canned text — so an unusable
    configuration is reported here rather than relayed.
    """
    provider_id = await resolve_llm_provider(session)
    spec = PROVIDERS.get(provider_id)
    if spec is None or not spec.openai_proxy:
        label = spec.label if spec else provider_id
        return ProxyAvailability(
            False,
            f"{label} can't back the OpenAI-compatible endpoint. Switch the provider "
            f"to one of: {', '.join(supported_provider_labels())}.",
        )
    if spec.uses_github_token:
        if not await resolve_github_token(session):
            return ProxyAvailability(
                False,
                f"{spec.label} needs a GitHub token (Settings → GitHub, or `gh auth login`).",
            )
        return ProxyAvailability(True)
    config = await resolve_llm_provider_config(session, provider_id)
    missing = [f.label for f in spec.fields if f.required and not config.get(f.name)]
    if missing:
        return ProxyAvailability(
            False, f"{spec.label} is missing its {', '.join(missing)} (Settings → Model)."
        )
    return ProxyAvailability(True)


def proxy_base_url() -> str:
    """The base URL an OpenAI client is configured with."""
    from precursor.backend.services.links import app_base_url

    return f"{app_base_url()}{BASE_PATH}"


async def rotate_proxy_key(session: AsyncSession) -> str:
    """Mint a new key, replacing any previous one. The caller commits."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    row = await session.get(AppSetting, KEY_SETTING)
    if row is None:
        session.add(AppSetting(key=KEY_SETTING, value=json.dumps(key)))
    else:
        row.value = json.dumps(key)
    return key


async def ensure_proxy_key(session: AsyncSession) -> str:
    """Return the key, minting one the first time. The caller commits."""
    return await resolve_openai_proxy_key(session) or await rotate_proxy_key(session)


async def authorize(session: AsyncSession, authorization: str | None) -> None:
    """Refuse the request unless the endpoint is on and the bearer key matches."""
    if not await resolve_openai_proxy_enabled(session):
        raise ProxyError(
            404,
            "Precursor's OpenAI-compatible endpoint is turned off. Enable it in Settings → Model.",
            code="endpoint_disabled",
        )
    expected = await resolve_openai_proxy_key(session)
    scheme, _, token = (authorization or "").partition(" ")
    if (
        not expected
        or scheme.lower() != "bearer"
        or not secrets.compare_digest(token.strip().encode(), expected.encode())
    ):
        raise ProxyError(
            401,
            "Invalid API key. Copy the key from Precursor's Settings → Model.",
            code="invalid_api_key",
        )


async def resolve_provider(session: AsyncSession) -> LLMProvider:
    availability = await proxy_availability(session)
    if not availability.available:
        raise ProxyError(503, availability.reason or "", code="provider_unavailable")
    provider = await get_llm_provider(session)
    # get_llm_provider also degrades to the mock when building the provider
    # fails outright, which the availability check can't foresee.
    if isinstance(provider, MockProvider):
        raise ProxyError(
            503,
            "The active provider could not be initialised. Check Settings → Model.",
            code="provider_unavailable",
        )
    return provider


# -- Models ----------------------------------------------------------------


def _model_object(model: LLMModel, owner: str) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": model.id,
        "object": "model",
        "created": 0,
        "owned_by": model.publisher or owner,
        # Not part of OpenAI's schema, but harmless to clients that ignore them
        # and exactly what a client needs to size requests for the model.
        "name": model.name,
        "vision": model.vision,
    }
    if model.context_window:
        obj["context_window"] = model.context_window
    if model.max_output_tokens:
        obj["max_output_tokens"] = model.max_output_tokens
    if model.supported_reasoning_efforts:
        obj["supported_reasoning_efforts"] = list(model.supported_reasoning_efforts)
    return obj


async def list_proxy_models(provider: LLMProvider) -> list[dict[str, Any]]:
    try:
        models = await provider.list_models()
    except Exception as exc:
        logger.warning("OpenAI endpoint: catalogue fetch from %s failed: %s", provider.name, exc)
        raise ProxyError(502, f"Could not fetch the model catalogue: {exc}") from exc
    return [_model_object(m, provider.name) for m in models]


# -- Request parsing ---------------------------------------------------------


@dataclass(slots=True)
class CompletionRequest:
    model: str
    messages: list[ChatMessage]
    tools: list[ToolDef] = field(default_factory=list)
    stream: bool = False
    include_usage: bool = False
    reasoning_effort: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def _content(raw: Any, param: str) -> tuple[str, list[str]]:
    """Split OpenAI message content into (text, image URLs)."""
    if raw is None:
        return "", []
    if isinstance(raw, str):
        return raw, []
    if not isinstance(raw, list):
        raise ProxyError(400, f"{param}.content must be a string or an array.", param=param)
    texts: list[str] = []
    images: list[str] = []
    for j, part in enumerate(raw):
        part_param = f"{param}.content[{j}]"
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            texts.append(str(part.get("text") or ""))
        elif kind == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str) or not url:
                raise ProxyError(400, f"{part_param}.image_url.url is required.", param=part_param)
            images.append(url)
        else:
            # Refused rather than skipped: silently dropping audio or a file
            # would have the model answer a question the user didn't ask.
            raise ProxyError(
                400, f"{part_param}: content type {kind!r} is not supported.", param=part_param
            )
    return "\n".join(t for t in texts if t), images


def _tool_calls(raw: Any, param: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProxyError(400, f"{param}.tool_calls must be an array.", param=param)
    calls: list[dict[str, Any]] = []
    for k, call in enumerate(raw):
        fn = call.get("function") if isinstance(call, dict) else None
        name = fn.get("name") if isinstance(fn, dict) else None
        if not isinstance(fn, dict) or not isinstance(name, str) or not name:
            call_param = f"{param}.tool_calls[{k}]"
            raise ProxyError(400, f"{call_param}.function.name is required.", param=call_param)
        arguments = fn.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        calls.append(
            {
                "id": str(call.get("id") or f"call_{k}"),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else "{}",
                },
            }
        )
    return calls


def _message(raw: Any, index: int) -> ChatMessage:
    param = f"messages[{index}]"
    if not isinstance(raw, dict):
        raise ProxyError(400, f"{param} must be an object.", param=param)
    role = _ROLES.get(str(raw.get("role")))
    if role is None:
        raise ProxyError(
            400, f"{param}.role {raw.get('role')!r} is not supported.", param=f"{param}.role"
        )
    text, images = _content(raw.get("content"), param)
    if images and role not in ("user", "tool"):
        raise ProxyError(
            400, f"{param}: images are only supported in user and tool messages.", param=param
        )
    message = ChatMessage(role=role, content=text, image_urls=images)
    if role == "assistant":
        message.tool_calls = _tool_calls(raw.get("tool_calls"), param) or None
    elif role == "tool":
        call_id = raw.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise ProxyError(400, f"{param}.tool_call_id is required.", param=param)
        message.tool_call_id = call_id
    return message


def _tools(raw: Any) -> list[ToolDef]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProxyError(400, "'tools' must be an array.", param="tools")
    tools: list[ToolDef] = []
    for i, tool in enumerate(raw):
        param = f"tools[{i}]"
        if not isinstance(tool, dict) or tool.get("type", "function") != "function":
            raise ProxyError(400, f"{param}: only function tools are supported.", param=param)
        fn = tool.get("function")
        name = fn.get("name") if isinstance(fn, dict) else None
        if not isinstance(fn, dict) or not isinstance(name, str) or not name:
            raise ProxyError(400, f"{param}.function.name is required.", param=param)
        parameters = fn.get("parameters")
        tools.append(
            ToolDef(
                name=name,
                description=str(fn.get("description") or ""),
                parameters=parameters
                if isinstance(parameters, dict)
                else {"type": "object", "properties": {}},
            )
        )
    return tools


_TOOL_IMAGES_NOTE = "Images returned by the tool calls above."


def _hoist_tool_images(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Move images out of tool results into a user message after them.

    Agent clients (VS Code among them) return a screenshot or an image file as
    ``image_url`` parts of the ``tool`` message, but providers only take images
    on user turns. The follow-up message goes after the whole run of tool
    results so every ``tool_calls`` entry stays directly followed by its answers.
    """
    out: list[ChatMessage] = []
    pending: list[str] = []
    for message in messages:
        if pending and message.role != "tool":
            out.append(ChatMessage(role="user", content=_TOOL_IMAGES_NOTE, image_urls=pending))
            pending = []
        if message.role == "tool" and message.image_urls:
            pending.extend(message.image_urls)
            message.image_urls = []
            if not message.content:
                message.content = "(image, attached in the next message)"
        out.append(message)
    if pending:
        out.append(ChatMessage(role="user", content=_TOOL_IMAGES_NOTE, image_urls=pending))
    return out


def parse_completion_request(body: Any) -> CompletionRequest:
    """Validate a chat-completions body into provider-layer types."""
    if not isinstance(body, dict):
        raise ProxyError(400, "The request body must be a JSON object.")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ProxyError(400, "'model' is required.", param="model")
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ProxyError(400, "'messages' must be a non-empty array.", param="messages")
    if body.get("n") not in (None, 1):
        raise ProxyError(400, "Only n=1 is supported.", param="n")

    tools = _tools(body.get("tools"))
    options = {
        key: body[key]
        for key in REQUEST_OPTIONS
        if body.get(key) is not None and (tools or key not in _TOOL_OPTIONS)
    }
    effort = body.get("reasoning_effort")
    reasoning = body.get("reasoning")
    if effort is None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    stream_options = body.get("stream_options")
    return CompletionRequest(
        model=model.strip(),
        messages=_hoist_tool_images([_message(m, i) for i, m in enumerate(raw_messages)]),
        tools=tools,
        stream=bool(body.get("stream")),
        include_usage=isinstance(stream_options, dict)
        and bool(stream_options.get("include_usage")),
        reasoning_effort=effort if isinstance(effort, str) and effort else None,
        options=options,
    )


# -- Relaying ----------------------------------------------------------------


def upstream_error(exc: BaseException) -> ProxyError:
    """Map a provider failure onto the status a client should see.

    The provider's own 4xx verdicts (bad parameter, unknown model, rate limit)
    pass through so clients react to them as they would with OpenAI. Its
    401/403 do not: those mean *Precursor's* provider credentials were refused,
    and relaying them would tell the client its own key is wrong.
    """
    origin = exc.__cause__ if isinstance(exc, LLMError) else exc
    status = getattr(origin, "status_code", None)
    message = str(exc) or type(exc).__name__
    if isinstance(status, int) and 400 <= status < 500 and status not in (401, 403):
        code = "rate_limit_exceeded" if status == 429 else "upstream_rejected"
        return ProxyError(status, message, code=code)
    return ProxyError(502, message, code="upstream_error")


async def _aclose(iterator: AsyncIterator[Any]) -> None:
    # A client that hangs up mid-reply should stop the upstream generation too,
    # rather than leave it streaming (and billing) until garbage collection.
    aclose = getattr(iterator, "aclose", None)
    if aclose is not None:
        await aclose()


async def _chain(
    first: ProviderEvent | None, rest: AsyncIterator[ProviderEvent]
) -> AsyncIterator[ProviderEvent]:
    try:
        if first is not None:
            yield first
        async for event in rest:
            yield event
    finally:
        await _aclose(rest)


async def open_completion(
    provider: LLMProvider, request: CompletionRequest
) -> AsyncIterator[ProviderEvent]:
    """Start the provider turn, surfacing a refusal before any byte is sent.

    Providers raise while *opening* their upstream stream, which only happens on
    the first iteration. Pulling one event here turns those refusals into a
    proper HTTP error instead of a half-started SSE response.
    """
    events = provider.stream_chat_with_tools(
        model=request.model,
        messages=request.messages,
        tools=request.tools,
        reasoning_effort=request.reasoning_effort,
        request_options=request.options or None,
    )
    first: ProviderEvent | None
    try:
        first = await events.__anext__()
    except StopAsyncIteration:
        first = None
    except Exception as exc:
        logger.warning("OpenAI endpoint: %s refused %s: %s", provider.name, request.model, exc)
        raise upstream_error(exc) from exc
    return _chain(first, events)


def _usage(usage: UsageEvent | None) -> dict[str, int]:
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens or usage.prompt_tokens + usage.completion_tokens,
    }


def _finish_reason(reported: str | None, called_tools: bool) -> str:
    if called_tools:
        return "tool_calls"
    return reported if reported in _FINISH_REASONS else "stop"


def _tool_call_objects(calls: list[ToolCallRequest], *, indexed: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, call in enumerate(calls):
        obj: dict[str, Any] = {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": call.arguments},
        }
        # Stream deltas address each call by position; a final message doesn't.
        out.append({"index": i, **obj} if indexed else obj)
    return out


async def _record_usage(model: str, usage: UsageEvent | None) -> None:
    if usage is None:
        return
    # The request-scoped session was released before the provider call, and a
    # streamed body outlives the request anyway.
    try:
        async with SessionLocal() as session:
            await record_usage(
                session,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                source=USAGE_SOURCE,
                model=model,
            )
            await session.commit()
    except Exception:
        logger.warning("OpenAI endpoint: recording token usage failed", exc_info=True)


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _sse(payload: dict[str, Any] | str) -> str:
    data = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    return f"data: {data}\n\n"


async def stream_completion(
    events: AsyncIterator[ProviderEvent], request: CompletionRequest
) -> AsyncIterator[str]:
    """Render provider events as chat-completion chunks over SSE."""
    completion_id = _completion_id()
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
        return _sse(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
        )

    yield chunk({"role": "assistant", "content": ""})
    usage: UsageEvent | None = None
    reported: str | None = None
    called_tools = False
    try:
        async for event in events:
            if isinstance(event, TextDeltaEvent):
                if event.content:
                    yield chunk({"content": event.content})
            elif isinstance(event, ReasoningDeltaEvent):
                # The field most clients read thinking from (VS Code, Open WebUI).
                if event.content:
                    yield chunk({"reasoning_content": event.content})
            elif isinstance(event, ToolCallsEvent):
                if event.calls:
                    called_tools = True
                    yield chunk({"tool_calls": _tool_call_objects(event.calls, indexed=True)})
            elif isinstance(event, UsageEvent):
                usage = event
            elif isinstance(event, TurnDoneEvent):
                reported = event.finish_reason
    except Exception as exc:
        # Headers are long gone; OpenAI itself reports a late failure in-band.
        logger.warning("OpenAI endpoint: stream from %s failed: %s", request.model, exc)
        yield _sse(upstream_error(exc).body())
        yield _sse("[DONE]")
        return
    finally:
        await _aclose(events)

    await _record_usage(request.model, usage)
    yield chunk({}, _finish_reason(reported, called_tools))
    if request.include_usage and usage is not None:
        yield _sse(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [],
                "usage": _usage(usage),
            }
        )
    yield _sse("[DONE]")


async def complete(
    events: AsyncIterator[ProviderEvent], request: CompletionRequest
) -> dict[str, Any]:
    """Collect provider events into a single chat.completion object."""
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCallRequest] = []
    usage: UsageEvent | None = None
    reported: str | None = None
    try:
        async for event in events:
            if isinstance(event, TextDeltaEvent):
                texts.append(event.content)
            elif isinstance(event, ReasoningDeltaEvent):
                reasoning.append(event.content)
            elif isinstance(event, ToolCallsEvent):
                calls.extend(event.calls)
            elif isinstance(event, UsageEvent):
                usage = event
            elif isinstance(event, TurnDoneEvent):
                reported = event.finish_reason
    except Exception as exc:
        logger.warning("OpenAI endpoint: completion from %s failed: %s", request.model, exc)
        raise upstream_error(exc) from exc

    await _record_usage(request.model, usage)
    text = "".join(texts)
    message: dict[str, Any] = {"role": "assistant", "content": text if text or not calls else None}
    if calls:
        message["tool_calls"] = _tool_call_objects(calls, indexed=False)
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _finish_reason(reported, bool(calls)),
                "logprobs": None,
            }
        ],
        "usage": _usage(usage),
    }
