"""Offline / no-token fallback provider — echoes a deterministic streamed reply.

Useful for development without a GITHUB_TOKEN and for tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from precursor.backend.services.llm.base import (
    ChatMessage,
    EmbeddingResult,
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


def _hashed_vector(text: str, size: int) -> list[float]:
    """Deterministic bag-of-stems vector, so offline similarity is meaningful.

    Words are accent-folded and cut to a 5-letter stem, so "clusters" and
    "clustering" land in the same bucket — enough for tests to tell a related
    passage from an unrelated one without a real embeddings endpoint.
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    vec = [0.0] * size
    for word in re.findall(r"\w+", folded):
        if len(word) < 3:
            continue
        digest = hashlib.blake2b(word[:5].encode(), digest_size=4).digest()
        vec[int.from_bytes(digest, "big") % size] += 1.0
    return vec


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

    async def embed(
        self, texts: Sequence[str], *, model: str, dimensions: int | None = None
    ) -> EmbeddingResult:
        _ = model
        size = dimensions or 256
        return EmbeddingResult(
            vectors=[_hashed_vector(t, size) for t in texts],
            prompt_tokens=sum(_rough_tokens(t) for t in texts),
        )

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
