"""LLM provider abstraction.

The active provider and its credentials live in the app settings (DB), resolved
per request. Providers are declared in :mod:`precursor.backend.services.llm.registry`
— add one there to onboard a new backend.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMProvider,
    TextDeltaEvent,
    UsageEvent,
)
from precursor.backend.services.llm.mock import MockProvider
from precursor.backend.services.llm.registry import PROVIDERS


async def get_llm_provider(
    session: AsyncSession, *, override_provider: str | None = None
) -> LLMProvider:
    """Build the configured provider, falling back to the mock when unusable.

    ``override_provider`` lets the catalog endpoint preview a provider the user
    has selected but not yet saved, using that provider's already-saved config.
    """
    from precursor.backend.services.app_settings import (
        resolve_llm_provider,
        resolve_llm_provider_config,
    )

    provider_id = (
        override_provider
        if override_provider and override_provider in PROVIDERS
        else await resolve_llm_provider(session)
    )
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        return MockProvider()
    if spec.uses_github_token:
        token = await resolve_github_token(session)
        if not token:
            return MockProvider()
        return spec.build({}, token)
    config = await resolve_llm_provider_config(session, provider_id)
    # A required field missing => the provider can't authenticate; surface the
    # mock so the app stays usable instead of erroring mid-stream.
    if any(f.required and not config.get(f.name) for f in spec.fields):
        return MockProvider()
    try:
        return spec.build(config, "")
    except Exception:  # defensive: a malformed config shouldn't 500 a chat turn
        return MockProvider()


__all__ = ["complete_text_with_usage", "get_llm_provider"]


async def complete_text_with_usage(
    provider: LLMProvider,
    *,
    model: str,
    messages: Sequence[ChatMessage],
    reasoning_effort: str | None = None,
) -> tuple[str, UsageEvent | None]:
    """Run a tool-less completion and return its text plus token usage.

    Utility callers (slash commands, issue-summary refresh, live meeting
    analysis) use this instead of ``stream_chat`` so the round-trip's token
    usage is captured and can be written to the usage ledger. Goes through
    ``stream_chat_with_tools`` with no tools because that path requests
    ``include_usage`` and emits a UsageEvent.
    """
    chunks: list[str] = []
    usage: UsageEvent | None = None
    async for event in provider.stream_chat_with_tools(
        model=model,
        messages=messages,
        tools=[],
        reasoning_effort=reasoning_effort or None,
    ):
        if isinstance(event, TextDeltaEvent):
            chunks.append(event.content)
        elif isinstance(event, UsageEvent):
            usage = event
    return "".join(chunks).strip(), usage
