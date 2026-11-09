"""Claude's native Messages API, for Claude deployments on Azure AI Foundry.

Foundry also serves Claude through the OpenAI-compatible APIs, but only the
native Messages API takes ``cache_control``. Caching matters more than price
here: on Foundry, cache reads don't count against a deployment's input-token
quota, so a large but stable prefix (tool definitions, system prompt, earlier
turns) stops eating a small tokens-per-minute budget on every round.

Differences from the OpenAI shapes this module folds back into Precursor's
provider events:

* the system prompt is a top-level ``system`` field, not a message;
* a tool call is a ``tool_use`` content block of the assistant turn, and its
  result a ``tool_result`` block of the next *user* turn, all results of one
  round in a single message;
* thinking comes back as signed ``thinking`` blocks that must be replayed
  unchanged on the next round of the same tool loop
  (``ChatMessage.provider_content`` carries them);
* effort is ``output_config.effort``; thinking can't be disabled on current
  Claude models, so it is always adaptive.

Cache breakpoints (at most four are allowed) go on the last tool definition, on
the system prompt, before the per-turn note that follows the history, and —
through top-level automatic caching — on the last message, so each round and
each follow-up turn reuses the previous request's prefix.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from anthropic import APIStatusError, AsyncAnthropic

from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    ProviderEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)

logger = logging.getLogger(__name__)

# Room for a long answer with adaptive thinking. Foundry doesn't reserve
# ``max_tokens`` against its output-token quota, so a high cap costs nothing.
DEFAULT_MAX_TOKENS = 32_000
_CACHE = {"type": "ephemeral"}
# Precursor's effort vocabulary, mapped onto Claude's (it has no "minimal").
_EFFORTS = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}
# Blocks that can't carry a cache breakpoint themselves.
_UNCACHEABLE_BLOCKS = frozenset({"thinking", "redacted_thinking"})
_THINKING_BLOCKS = _UNCACHEABLE_BLOCKS
# Private key on a replayed thinking block: the fingerprint of the request
# prefix (tools, system prompt, earlier messages) it was written after. Claude
# binds a signed thinking block to that prefix and rejects it once anything in
# it changed — a tool loaded on demand, trimmed history — so such a block is
# dropped instead of replayed, as the API asks. Never sent.
_PREFIX_KEY = "_precursor_prefix"
_DATA_URL_RE = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,(.*)$", re.DOTALL)
# Content-block fields the API accepts back, per block type. Response blocks
# carry extra (often ``None``) fields that a request would reject.
_REPLAY_FIELDS: dict[str, tuple[str, ...]] = {
    "text": ("type", "text"),
    "thinking": ("type", "thinking", "signature"),
    "redacted_thinking": ("type", "data"),
    "tool_use": ("type", "id", "name", "input"),
}


def _image_block(url: str) -> dict[str, Any]:
    match = _DATA_URL_RE.match(url)
    if match:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": match.group(1), "data": match.group(2)},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _tool_input(arguments: str) -> dict[str, Any]:
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _prefix_fingerprint(
    tools: Sequence[ToolDef], system: list[dict[str, Any]], chat: list[dict[str, Any]]
) -> str:
    # Cache breakpoints move from one request to the next; they aren't content.
    messages = [
        {
            "role": m["role"],
            "content": [{k: v for k, v in b.items() if k != "cache_control"} for b in m["content"]],
        }
        for m in chat
    ]
    payload = {
        "tools": [[t.name, t.description, t.parameters] for t in tools],
        "system": [b.get("text") for b in system],
        "messages": messages,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _assistant_blocks(m: ChatMessage, prefix: str | None) -> list[dict[str, Any]]:
    if m.provider_content:
        return [
            {k: v for k, v in block.items() if k != _PREFIX_KEY}
            for block in m.provider_content
            if block.get("type") not in _THINKING_BLOCKS or block.get(_PREFIX_KEY) == prefix
        ]
    blocks: list[dict[str, Any]] = []
    if m.content:
        blocks.append({"type": "text", "text": m.content})
    for call in m.tool_calls or []:
        fn = call.get("function") or {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id") or "",
                "name": fn.get("name") or "",
                "input": _tool_input(fn.get("arguments") or ""),
            }
        )
    return blocks


def to_anthropic_request(
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDef] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split chat messages into Claude's ``system`` blocks and ``messages``.

    Leading system messages form the cacheable system prompt. A system message
    later in the history (Precursor stores a few, e.g. notices) becomes a user
    note instead: moving it into ``system`` would change the cached prefix.
    Consecutive turns of the same role are merged, so the tool results of one
    round share a single user message as the API requires.
    """
    system: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    # The last block before a mid-history system note (e.g. the per-turn
    # message references): the conversation up to there is stable from one
    # turn to the next, so it gets a cache breakpoint of its own.
    before_note: dict[str, Any] | None = None

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if m.role == "system":
            if not out:
                if m.content:
                    system.append({"type": "text", "text": m.content})
            elif m.content:
                cacheable = [
                    b for b in out[-1]["content"] if b.get("type") not in _UNCACHEABLE_BLOCKS
                ]
                if cacheable:
                    before_note = cacheable[-1]
                add("user", [{"type": "text", "text": f"[System note]\n{m.content}"}])
        elif m.role == "tool":
            add(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content or "(no output)",
                    }
                ],
            )
        elif m.role == "assistant":
            # Only computed when there is a signed turn to check against it.
            prefix = (
                _prefix_fingerprint(tools, system, out)
                if any(b.get("type") in _THINKING_BLOCKS for b in m.provider_content or [])
                else None
            )
            add("assistant", _assistant_blocks(m, prefix))
        else:
            blocks = [_image_block(url) for url in m.image_urls]
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            add("user", blocks)
    if system:
        system[-1]["cache_control"] = _CACHE
    if before_note is not None:
        before_note["cache_control"] = _CACHE
    return system, out


def to_anthropic_tools(tools: Sequence[ToolDef]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]
    if out:
        out[-1]["cache_control"] = _CACHE
    return out


def to_anthropic_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Translate chat-completions request options to Messages parameters.

    Sampling options are dropped: current Claude models reject them. A forced
    ``tool_choice`` becomes ``auto`` because current models reject forcing too;
    the caller's prompt still asks for the tool.
    """
    out: dict[str, Any] = {}
    if not options:
        return out
    max_tokens = options.get("max_completion_tokens", options.get("max_tokens"))
    if isinstance(max_tokens, int) and max_tokens > 0:
        out["max_tokens"] = max_tokens
    choice = options.get("tool_choice")
    if choice == "none":
        out["tool_choice"] = {"type": "none"}
    elif choice is not None:
        out["tool_choice"] = {"type": "auto"}
    if options.get("parallel_tool_calls") is False:
        out["tool_choice"] = {**out.get("tool_choice", {"type": "auto"})}
        out["tool_choice"]["disable_parallel_tool_use"] = True
    fmt = options.get("response_format")
    if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
        schema = (fmt.get("json_schema") or {}).get("schema")
        if isinstance(schema, dict):
            out["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    stop = options.get("stop")
    if isinstance(stop, str):
        out["stop_sequences"] = [stop]
    elif isinstance(stop, list) and stop:
        out["stop_sequences"] = [s for s in stop if isinstance(s, str)]
    return out


def _friendly_error(exc: APIStatusError) -> LLMError:
    message = getattr(exc, "message", "") or str(exc)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
    if exc.status_code in (401, 403):
        return LLMError(
            "The model provider rejected the credentials (check the API key / "
            "endpoint in Settings → Model)."
        )
    if exc.status_code == 429:
        return LLMError(f"The model provider's rate limit was reached: {message}")
    return LLMError(f"The model provider rejected the request: {message}")


def _replayable(block: Any) -> dict[str, Any] | None:
    data = block.model_dump(mode="json") if hasattr(block, "model_dump") else dict(block)
    fields = _REPLAY_FIELDS.get(str(data.get("type")))
    if fields is None:
        return None
    return {k: data[k] for k in fields if k in data}


async def stream_anthropic_tools(
    *,
    client: AsyncAnthropic,
    model: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDef],
    reasoning_effort: str | None = None,
    request_options: Mapping[str, Any] | None = None,
) -> AsyncIterator[ProviderEvent]:
    """Stream one Claude round, translated into Precursor's provider events."""
    system, chat = to_anthropic_request(messages, tools)
    # Taken before cache breakpoints are added: what a thinking block written
    # in reply to this request is bound to.
    prefix = _prefix_fingerprint(tools, system, chat)
    options = to_anthropic_options(request_options)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": options.pop("max_tokens", DEFAULT_MAX_TOKENS),
        "messages": chat,
        # Caches the growing conversation tail on top of the explicit
        # breakpoints on the tools and the system prompt.
        "cache_control": _CACHE,
        "thinking": {"type": "adaptive", "display": "summarized"},
        **options,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = to_anthropic_tools(tools)
    effort = _EFFORTS.get((reasoning_effort or "").lower())
    if effort:
        kwargs["output_config"] = {**kwargs.get("output_config", {}), "effort": effort}

    if logger.isEnabledFor(logging.DEBUG):
        # Fingerprints of the cacheable prefix: when cache reads drop, the first
        # part whose fingerprint changed between rounds is the invalidator.
        def digest(value: Any) -> str:
            return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:10]

        logger.debug(
            "claude request model=%s tools=%s(%d) system=%s messages=%d",
            model,
            digest(kwargs.get("tools", [])),
            len(kwargs.get("tools", [])),
            digest(kwargs.get("system", [])),
            len(chat),
        )
    try:
        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "text_delta" and delta.text:
                    yield TextDeltaEvent(content=delta.text)
                elif delta.type == "thinking_delta" and delta.thinking:
                    yield ReasoningDeltaEvent(content=delta.thinking)
            final = await stream.get_final_message()
    except APIStatusError as exc:
        raise _friendly_error(exc) from exc

    calls = [
        ToolCallRequest(id=block.id, name=block.name, arguments=json.dumps(block.input))
        for block in final.content
        if block.type == "tool_use"
    ]
    if calls:
        replay = [b for b in (_replayable(block) for block in final.content) if b]
        for block in replay:
            if block.get("type") in _THINKING_BLOCKS:
                block[_PREFIX_KEY] = prefix
        yield ToolCallsEvent(calls=calls, provider_content=replay)

    usage = final.usage
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_creation_input_tokens or 0
    prompt = (usage.input_tokens or 0) + cache_read + cache_write
    completion = usage.output_tokens or 0
    yield UsageEvent(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )

    stop = final.stop_reason
    finish = {"tool_use": "tool_calls", "max_tokens": "length"}.get(stop or "", stop or "stop")
    yield TurnDoneEvent(finish_reason="stop" if finish == "end_turn" else finish)
