"""Azure AI Foundry / Azure OpenAI provider.

Targets an Azure OpenAI / AI Foundry chat deployment via the OpenAI SDK's
``AsyncAzureOpenAI`` client, which speaks the Azure URL scheme
(``{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=…``)
and the ``api-key`` header. The model id passed to ``stream_chat`` is the Azure
**deployment name**.

Azure doesn't expose a uniform per-key model catalog here, so ``list_models``
returns the configured deployment (if any) and the UI falls back to manual
deployment-name entry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import AsyncAzureOpenAI

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

DEFAULT_API_VERSION = "2024-10-21"


class AzureFoundryProvider:
    name = "azure_foundry"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        api_version: str = DEFAULT_API_VERSION,
        deployment: str = "",
    ) -> None:
        self._deployment = deployment
        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint.rstrip("/"),
            api_key=api_key,
            api_version=api_version or DEFAULT_API_VERSION,
        )

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {
            "model": model or self._deployment,
            "messages": to_openai_messages(messages),
            "stream": True,
        }
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
    ) -> AsyncIterator[ProviderEvent]:
        async for event in stream_openai_tools(
            client=self._client,
            model=model or self._deployment,
            messages=messages,
            tools=tools,
        ):
            yield event

    async def list_models(self) -> list[LLMModel]:
        # No portable catalog endpoint; surface the configured deployment so the
        # picker has at least one entry, otherwise the UI uses manual entry.
        if self._deployment:
            return [LLMModel(id=self._deployment, name=self._deployment, publisher="Azure")]
        return []
