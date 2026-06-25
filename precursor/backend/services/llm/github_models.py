"""GitHub Models provider — OpenAI-compatible endpoint at https://models.github.ai/inference.

Authenticates with a GitHub PAT (fine-grained ``models:read`` scope, or a
classic ``GITHUB_TOKEN`` when running inside GitHub Actions).
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

GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"
GITHUB_MODELS_CATALOG_URL = "https://models.github.ai/catalog/models"


class GitHubModelsProvider:
    name = "github_models"

    def __init__(self, *, token: str, base_url: str = GITHUB_MODELS_BASE_URL) -> None:
        self._token = token
        self._client = AsyncOpenAI(base_url=base_url, api_key=token)

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
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(GITHUB_MODELS_CATALOG_URL, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        items = payload.get("data", payload) if isinstance(payload, dict) else payload
        models: list[LLMModel] = []
        for item in items:
            mid = item.get("id") or item.get("name")
            if not mid:
                continue
            limits = item.get("limits") or {}
            ctx = (
                limits.get("max_input_tokens")
                or limits.get("max_prompt_tokens")
                or item.get("max_input_tokens")
            )
            # The catalog doesn't currently advertise a reasoning-effort set;
            # read it best-effort so we pick it up automatically if it appears.
            efforts = item.get("supported_reasoning_efforts")
            efforts = [str(e) for e in efforts] if isinstance(efforts, list) else []
            models.append(
                LLMModel(
                    id=mid,
                    name=item.get("friendly_name") or item.get("name") or mid,
                    publisher=item.get("publisher", ""),
                    summary=item.get("summary", ""),
                    tags=list(item.get("tags") or []),
                    context_window=int(ctx) if isinstance(ctx, (int, float)) else None,
                    supported_reasoning_efforts=efforts,
                )
            )
        models.sort(key=lambda m: (m.publisher.lower(), m.name.lower()))
        return models
