"""Generic OpenAI-compatible LLM provider.

Many providers speak the OpenAI Chat Completions wire format — OpenAI itself,
Mistral, Ollama, Hugging Face's router, and most self-hosted gateways. This
single class drives all of them: it's just an ``AsyncOpenAI`` client pointed at
the provider's ``base_url`` with its API key, reusing the shared streaming +
tool helpers. Model discovery uses the standard ``GET {base_url}/models``
endpoint when available; callers fall back to manual model entry when it isn't.
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


class OpenAICompatibleProvider:
    """OpenAI-compatible provider parameterised by base URL + key."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        publisher: str = "",
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._publisher = publisher
        # Ollama and some gateways accept any key; pass a placeholder so the SDK
        # (which requires a non-empty api_key) still constructs.
        self._client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=api_key or "not-needed",
            default_headers=default_headers or None,
        )

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
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
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self._base_url}/models", headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        items = payload.get("data", []) if isinstance(payload, dict) else payload
        models: list[LLMModel] = []
        for item in items or []:
            mid = item.get("id") if isinstance(item, dict) else None
            if not mid:
                continue
            models.append(
                LLMModel(
                    id=mid,
                    name=item.get("name") or mid,
                    publisher=item.get("owned_by") or self._publisher,
                )
            )
        return models
