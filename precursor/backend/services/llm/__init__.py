"""LLM provider abstraction.

Ships GitHub Models (OpenAI-compatible) and a Mock provider for offline dev.
Future providers can be added by implementing ``LLMProvider`` and registering
them in ``get_llm_provider``.
"""

from __future__ import annotations

from functools import lru_cache

from precursor.backend.config import get_settings
from precursor.backend.services.llm.base import LLMProvider
from precursor.backend.services.llm.github_models import GitHubModelsProvider
from precursor.backend.services.llm.mock import MockProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "mock" or not settings.github_token:
        return MockProvider()
    return GitHubModelsProvider(token=settings.github_token)


__all__ = ["GitHubModelsProvider", "LLMProvider", "MockProvider", "get_llm_provider"]
