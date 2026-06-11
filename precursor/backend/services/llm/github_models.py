"""GitHub Models provider — OpenAI-compatible endpoint at https://models.github.ai/inference.

Authenticates with a GitHub PAT (fine-grained ``models:read`` scope, or a
classic ``GITHUB_TOKEN`` when running inside GitHub Actions).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from openai import AsyncOpenAI

from precursor.backend.services.llm.base import ChatMessage

GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"


class GitHubModelsProvider:
    name = "github_models"

    def __init__(self, *, token: str, base_url: str = GITHUB_MODELS_BASE_URL) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=token)

    async def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
