"""Offline / no-token fallback provider — echoes a deterministic streamed reply.

Useful for development without a GITHUB_TOKEN and for tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMModel,
    ProviderEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)

_MOCK_REASONING = (
    "**Reading the request**\n\n",
    "No model provider is configured, ",
    "so I'll echo the last user message back.",
)


def _rough_tokens(text: str) -> int:
    # OpenAI-style rule of thumb: ~4 chars per token. Enough for a UI estimate.
    return max(1, len(text) // 4)


class MockProvider:
    name = "mock"

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        _ = reasoning_effort
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "(no user message)",
        )
        reply = (
            f"**[mock:{model}]** I received: _{last_user.strip()[:200]}_. "
            "Configure `GITHUB_TOKEN` to enable real GitHub Copilot responses."
        )
        for token in reply.split(" "):
            yield token + " "
            await asyncio.sleep(0.02)

    async def stream_chat_with_tools(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
        reasoning_effort: str | None = None,
        request_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        # Mock never issues tool calls; replay the plain text path.
        _ = tools, request_options
        if reasoning_effort:
            # Think out loud like a reasoning model, so the thinking disclosure
            # can be exercised without a real provider.
            for part in _MOCK_REASONING:
                yield ReasoningDeltaEvent(content=part)
                await asyncio.sleep(0.05)
        chunks: list[str] = []
        async for chunk in self.stream_chat(model=model, messages=messages):
            chunks.append(chunk)
            yield TextDeltaEvent(content=chunk)
        prompt_tokens = sum(_rough_tokens(m.content) for m in messages)
        completion_tokens = _rough_tokens("".join(chunks))
        yield UsageEvent(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        yield TurnDoneEvent(finish_reason="stop")

    async def list_models(self) -> list[LLMModel]:
        return [
            LLMModel(
                id="mock",
                name="Mock",
                publisher="precursor",
                summary="Deterministic offline echo provider.",
                context_window=8000,
                supported_reasoning_efforts=["low", "medium", "high"],
            )
        ]
