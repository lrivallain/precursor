"""LLM provider abstraction.

The active provider and its credentials live in the app settings (DB), resolved
per request. Providers are declared in :mod:`precursor.backend.services.llm.registry`
— add one there to onboard a new backend.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm.base import LLMProvider
from precursor.backend.services.llm.mock import MockProvider
from precursor.backend.services.llm.registry import PROVIDERS


async def get_llm_provider(session: AsyncSession) -> LLMProvider:
    """Build the configured provider, falling back to the mock when unusable."""
    from precursor.backend.services.app_settings import (
        resolve_llm_provider,
        resolve_llm_provider_config,
    )

    provider_id = await resolve_llm_provider(session)
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


__all__ = ["get_llm_provider"]


__all__ = [
    "GitHubCopilotProvider",
    "GitHubModelsProvider",
    "LLMProvider",
    "MockProvider",
    "get_llm_provider",
]
