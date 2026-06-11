"""Offline / no-token fallback provider — echoes a deterministic streamed reply.

Useful for development without a GITHUB_TOKEN and for tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from precursor.backend.services.llm.base import ChatMessage


class MockProvider:
    name = "mock"

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "(no user message)",
        )
        reply = (
            f"**[mock:{model}]** I received: _{last_user.strip()[:200]}_. "
            "Configure `GITHUB_TOKEN` to enable real GitHub Models responses."
        )
        for token in reply.split(" "):
            yield token + " "
            await asyncio.sleep(0.02)
