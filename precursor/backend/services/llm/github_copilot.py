"""GitHub Copilot provider — https://api.githubcopilot.com.

Surfaces the full Copilot model catalogue (Claude, Gemini, GPT, etc.) for users
with an active Copilot subscription. Uses the same ``GITHUB_TOKEN`` (a
``gho_*`` token from ``gh auth login``) for both catalog and inference.

Copilot serves models over two API surfaces and models don't overlap: Claude and
the older GPTs answer on ``/chat/completions``, while GPT-5.5+ and Grok are
reachable only through the Responses API. The catalogue reports this per model
as ``supported_endpoints``, so the provider records it and routes each turn to
the endpoint that model actually serves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import httpx
from openai import AsyncOpenAI

from precursor.backend.services.llm._openai_compat import (
    ModelNotEntitledError,
    UnsupportedEndpointError,
    stream_openai_tools,
)
from precursor.backend.services.llm._responses_compat import stream_responses_tools
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMModel,
    ProviderEvent,
    TextDeltaEvent,
    ToolDef,
)

COPILOT_API_BASE_URL = "https://api.githubcopilot.com"
COPILOT_EDITOR_VERSION = "precursor/0.1.0"
COPILOT_INTEGRATION_ID = "vscode-chat"

CHAT_COMPLETIONS_ENDPOINT = "/chat/completions"
RESPONSES_ENDPOINT = "/responses"
# Endpoints this provider knows how to drive. A model serving neither (e.g. only
# Anthropic's native /v1/messages) is dropped from the catalogue rather than
# offered as a selection that would fail on first use.
CALLABLE_ENDPOINTS = frozenset({CHAT_COMPLETIONS_ENDPOINT, RESPONSES_ENDPOINT})

# model id -> endpoints it advertises, learned from /models and corrected when
# the API rejects one. Module-level because the registry builds a fresh provider
# per request, so instance state wouldn't survive to the next turn.
_MODEL_ENDPOINTS: dict[str, frozenset[str]] = {}


def _prefers_responses(model: str) -> bool:
    """Whether this model must be driven through the Responses API."""
    endpoints = _MODEL_ENDPOINTS.get(model)
    if not endpoints:
        # Unknown model: try chat-completions and let the error path correct us.
        return False
    return CHAT_COMPLETIONS_ENDPOINT not in endpoints and RESPONSES_ENDPOINT in endpoints


def _copilot_headers() -> dict[str, str]:
    return {
        "Editor-Version": COPILOT_EDITOR_VERSION,
        "Copilot-Integration-Id": COPILOT_INTEGRATION_ID,
    }


class GitHubCopilotProvider:
    name = "github_copilot"

    def __init__(self, *, token: str, base_url: str = COPILOT_API_BASE_URL) -> None:
        self._token = token
        self._base_url = base_url
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=token,
            default_headers=_copilot_headers(),
        )

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        # Delegated so both entry points share one endpoint-routing decision.
        async for event in self.stream_chat_with_tools(
            model=model,
            messages=messages,
            tools=(),
            reasoning_effort=reasoning_effort,
        ):
            if isinstance(event, TextDeltaEvent) and event.content:
                yield event.content

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        if not _prefers_responses(model):
            # The rejection is raised while opening the stream, before anything
            # is yielded, so falling back can't duplicate output. Guard anyway.
            yielded = False
            try:
                async for event in stream_openai_tools(
                    client=self._client,
                    model=model,
                    messages=messages,
                    tools=tools,
                    reasoning_effort=reasoning_effort,
                ):
                    yielded = True
                    yield event
                return
            except UnsupportedEndpointError:
                if yielded:
                    raise
                # Catalogue was stale or never fetched — remember the correction
                # so the next turn routes straight to the Responses API.
                _MODEL_ENDPOINTS[model] = frozenset({RESPONSES_ENDPOINT})
            except ModelNotEntitledError:
                # Copilot answers this — rather than ``unsupported_api_for_model``
                # — for some Responses-only models called on chat-completions,
                # so an *unknown* model gets a second guess on the other surface.
                # A model the catalogue says serves chat-completions doesn't:
                # there the rejection is about entitlement, not the endpoint.
                if yielded or model in _MODEL_ENDPOINTS:
                    raise
                _MODEL_ENDPOINTS[model] = frozenset({RESPONSES_ENDPOINT})

        async for event in stream_responses_tools(
            client=self._client,
            model=model,
            messages=messages,
            tools=tools,
            reasoning_effort=reasoning_effort,
        ):
            yield event

    async def list_models(self) -> list[LLMModel]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            **_copilot_headers(),
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self._base_url}/models", headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        items = payload.get("data", []) if isinstance(payload, dict) else payload
        models: list[LLMModel] = []
        for item in items:
            mid = item.get("id")
            if not mid:
                continue
            # Filter to chat-capable models the picker exposes.
            capabilities = item.get("capabilities") or {}
            if capabilities.get("type") and capabilities["type"] != "chat":
                continue
            if not item.get("model_picker_enabled", True):
                continue
            # The catalogue advertises which API surfaces serve each model, and
            # they differ: newer models are Responses-only, Claude adds its
            # native /v1/messages. Record the set so chat routes to the right
            # endpoint, and skip anything we can't drive at all. Entries that
            # omit the field predate it — assume chat-completions.
            endpoints = item.get("supported_endpoints")
            endpoints = (
                frozenset(str(e) for e in endpoints) if isinstance(endpoints, list) else frozenset()
            )
            if endpoints:
                if not endpoints & CALLABLE_ENDPOINTS:
                    continue
                _MODEL_ENDPOINTS[mid] = endpoints
            limits = capabilities.get("limits") or {}
            ctx = limits.get("max_prompt_tokens") or limits.get("max_context_window_tokens")
            supports = capabilities.get("supports") or {}
            efforts = supports.get("reasoning_effort")
            efforts = [str(e) for e in efforts] if isinstance(efforts, list) else []
            models.append(
                LLMModel(
                    id=mid,
                    name=item.get("name") or mid,
                    publisher=item.get("vendor", ""),
                    summary=item.get("version", ""),
                    tags=[],
                    context_window=int(ctx) if isinstance(ctx, (int, float)) else None,
                    supported_reasoning_efforts=efforts,
                )
            )
        models.sort(key=lambda m: (m.publisher.lower(), m.name.lower()))
        return models
