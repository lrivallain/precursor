"""GitHub Copilot provider — OpenAI-compatible endpoint at https://api.githubcopilot.com.

Surfaces the full Copilot model catalogue (Claude, Gemini, GPT, etc.) for users
with an active Copilot subscription. Uses the same ``GITHUB_TOKEN`` (a
``gho_*`` token from ``gh auth login``) for both catalog and inference.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from openai import AsyncOpenAI

from precursor.backend.services.llm._openai_compat import (
    stream_openai_tools,
    to_openai_messages,
)
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMModel,
    ProviderEvent,
    ToolDef,
)

COPILOT_API_BASE_URL = "https://api.githubcopilot.com"
COPILOT_EDITOR_VERSION = "precursor/0.1.0"
COPILOT_INTEGRATION_ID = "vscode-chat"


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
        # Any-typed kwargs so the create() overload resolves to a stream (the
        # explicit-arg form returns a ChatCompletion | AsyncStream union).
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(messages),
            "stream": True,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        async for event in stream_openai_tools(
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
