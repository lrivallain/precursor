"""LLM provider abstraction.

The active provider and its credentials live in the app settings (DB), resolved
per request. Providers are declared in :mod:`precursor.backend.services.llm.registry`
— add one there to onboard a new backend.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.schemas.model_fallback import AGENTS_PROVIDER
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    TextDeltaEvent,
    UsageEvent,
)
from precursor.backend.services.llm.mock import MockProvider
from precursor.backend.services.llm.registry import PROVIDERS


class ProviderConfigurationError(LLMError):
    """An explicitly requested provider cannot be constructed."""


async def _build_provider(
    session: AsyncSession, provider_id: str, *, strict: bool, keep_invalid: bool = False
) -> LLMProvider | None:
    """Build ``provider_id``; the mock when unusable and ``strict`` is off.

    ``None`` means the provider's config is invalid and the caller should serve
    the mock without any preset wrapper.
    """
    from precursor.backend.services.app_settings import (
        resolve_llm_provider_config,
        resolve_mock_provider_enabled,
    )

    if provider_id != "mock" and not strict and not await resolve_mock_provider_enabled(session):
        strict = True
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        if strict:
            raise ProviderConfigurationError("Unknown LLM provider.")
        return MockProvider()
    if spec.uses_github_token:
        token = await resolve_github_token(session)
        if token:
            return spec.build({}, token)
        if strict:
            raise ProviderConfigurationError(
                f"{spec.label} is not configured. Connect GitHub in Settings > GitHub."
            )
        return MockProvider()
    config = await resolve_llm_provider_config(session, provider_id)
    if any(f.required and not config.get(f.name) for f in spec.fields):
        if strict:
            raise ProviderConfigurationError(
                f"{spec.label} is not configured. Check credentials in Settings > Model."
            )
        return MockProvider()
    try:
        return spec.build(config, "")
    except Exception:
        if strict:
            raise ProviderConfigurationError(
                f"{spec.label} configuration is invalid. Check Settings > Model."
            ) from None
        return MockProvider() if keep_invalid else None


async def get_llm_provider(
    session: AsyncSession,
    *,
    override_provider: str | None = None,
    model_category: str | None = None,
    strict: bool = False,
    context_tokens: int | None = None,
) -> LLMProvider:
    """Build the configured provider, falling back to the mock when unusable.

    ``override_provider`` lets the catalog endpoint preview a provider the user
    has selected but not yet saved, using that provider's already-saved config.
    A ``model_category`` returns a router that serves each preset from the
    preset's own provider, so one category can mix providers.
    """
    from precursor.backend.services.app_settings import (
        resolve_llm_max_input_tokens,
        resolve_llm_provider,
    )
    from precursor.backend.services.llm.fallback import CategoryFallbackProvider
    from precursor.backend.services.model_fallbacks import resolve_all_model_categories

    strict_base = strict and model_category is None
    if strict_base and override_provider not in PROVIDERS:
        raise ProviderConfigurationError("Unknown LLM provider.")
    provider_id = (
        override_provider
        if override_provider and override_provider in PROVIDERS
        else await resolve_llm_provider(session)
    )
    provider = await _build_provider(
        session, provider_id, strict=strict_base, keep_invalid=model_category is not None
    )
    if provider is None:
        return MockProvider()
    if model_category is None and isinstance(provider, MockProvider) and provider_id != "mock":
        return provider
    categories = await resolve_all_model_categories(session)
    llm_presets = [
        p for c in categories.categories for p in c.presets if p.provider != AGENTS_PROVIDER
    ]
    if model_category is None and not llm_presets:
        return provider
    providers: dict[str, LLMProvider | LLMError] = {}
    if not isinstance(provider, MockProvider):
        providers[provider_id] = provider
    for needed in {p.provider for p in llm_presets} - set(providers):
        try:
            built = await _build_provider(session, needed, strict=True)
        except ProviderConfigurationError as exc:
            providers[needed] = exc
        else:
            if built is not None:
                providers[needed] = built
    return CategoryFallbackProvider(
        provider,
        categories,
        context_tokens
        if context_tokens is not None
        else await resolve_llm_max_input_tokens(session),
        provider_id=provider_id,
        selected_category=model_category,
        providers=providers,
    )


__all__ = ["complete_text_with_usage", "get_llm_provider"]


async def complete_text_with_usage(
    provider: LLMProvider,
    *,
    model: str,
    messages: Sequence[ChatMessage],
    reasoning_effort: str | None = None,
) -> tuple[str, UsageEvent | None]:
    """Run a tool-less completion and return its text plus token usage.

    Provider-level and session-free. Features call
    :func:`precursor.backend.services.llm.one_shot.complete_once`, which wraps
    this with model resolution and usage accounting. Goes through
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
