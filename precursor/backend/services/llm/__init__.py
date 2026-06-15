"""LLM provider abstraction.

Ships GitHub Copilot, GitHub Models (both OpenAI-compatible), and a Mock
provider for offline dev. Future providers can be added by implementing
``LLMProvider`` and registering them in ``get_llm_provider``.
"""

from __future__ import annotations

from functools import lru_cache

from precursor.backend.config import get_settings
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm.base import LLMProvider
from precursor.backend.services.llm.github_copilot import GitHubCopilotProvider
from precursor.backend.services.llm.github_models import GitHubModelsProvider
from precursor.backend.services.llm.mock import MockProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    token = resolve_github_token(settings)
    if settings.llm_provider == "mock" or not token:
        return MockProvider()
    if settings.llm_provider == "github_models":
        return GitHubModelsProvider(token=token)
    return GitHubCopilotProvider(token=token)


__all__ = [
    "GitHubCopilotProvider",
    "GitHubModelsProvider",
    "LLMProvider",
    "MockProvider",
    "get_llm_provider",
]
