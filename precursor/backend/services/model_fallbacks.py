"""Shared category matching and narrowly scoped rejection recovery."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.schemas.model_fallback import (
    AGENTS_PROVIDER,
    ModelCategories,
    ModelCategoryDef,
    ModelPreset,
    normalize_stored_categories,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSelection:
    model: str
    provider: str = ""
    reasoning_effort: str = ""
    context_tokens: int = 128_000
    context_tier: str = "default"


def scoped_categories(categories: ModelCategories, scope: str) -> ModelCategories:
    """Keep presets for ``scope``: ``agents``, ``llm`` (every non-agent provider) or a provider id."""

    def keep(preset: ModelPreset) -> bool:
        if scope == AGENTS_PROVIDER:
            return preset.provider == AGENTS_PROVIDER
        if scope == "llm":
            return preset.provider != AGENTS_PROVIDER
        return preset.provider == scope

    return ModelCategories(
        categories=[
            c.model_copy(update={"presets": [p for p in c.presets if keep(p)]})
            for c in categories.categories
        ]
    )


async def resolve_all_model_categories(session: AsyncSession) -> ModelCategories:
    from precursor.backend.services.app_settings import _get_db_value

    raw = await _get_db_value(session, "model_fallbacks")
    try:
        return normalize_stored_categories(raw)
    except ValidationError:
        logger.exception("Invalid saved model preset configuration")
        return ModelCategories()


async def resolve_model_fallbacks(session: AsyncSession, scope: str = "llm") -> ModelCategories:
    return scoped_categories(await resolve_all_model_categories(session), scope)


async def resolve_model_category(session: AsyncSession, *, agents: bool = False) -> str | None:
    from precursor.backend.services.app_settings import _get_db_value

    key = "agents_model_category" if agents else "llm_model_category"
    raw = await _get_db_value(session, key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        logger.warning("Invalid selected model category for %s: %r", key, raw)
        return None
    return raw


def selected_category_presets(categories: ModelCategories, category: str) -> list[ModelPreset]:
    found = categories.get(category)
    return list(found.presets) if found is not None else []


async def preview_llm_category_preset(session: AsyncSession) -> ModelPreset | None:
    from precursor.backend.services.app_settings import resolve_llm_provider
    from precursor.backend.services.model_catalog import offered_model_ids

    category = await resolve_model_category(session)
    if category is None:
        return None
    categories = await resolve_model_fallbacks(session)
    presets = selected_category_presets(categories, category)
    if not presets:
        return None
    default_provider = await resolve_llm_provider(session)
    offered = await offered_model_ids(session)
    # A preview must never block Settings when all configured models disappear.
    # Only the default provider's catalogue is cached here; others are trusted.
    return next(
        (
            preset
            for preset in presets
            if preset.provider != default_provider or not offered or preset.model in offered
        ),
        presets[0],
    )


async def resolve_llm_preparation_budget(
    session: AsyncSession, *, topic_id: int | None = None, chat_id: int | None = None
) -> int:
    from precursor.backend.services.app_settings import resolve_llm_max_input_tokens
    from precursor.backend.services.conversation_llm import saved_selection

    selection = await saved_selection(session, topic_id=topic_id, chat_id=chat_id)
    if selection is not None:
        if selection.category:
            categories = await resolve_model_fallbacks(session)
            presets = selected_category_presets(categories, selection.category)
            if presets:
                return max(preset.context_tokens for preset in presets)
        return selection.context_tokens

    preset = await preview_llm_category_preset(session)
    return (
        preset.context_tokens if preset is not None else await resolve_llm_max_input_tokens(session)
    )


def matching_category(
    categories: ModelCategories,
    selection: ModelSelection | ModelPreset,
    *,
    agents: bool = False,
) -> ModelCategoryDef | None:
    # Exact configuration wins when the same model occupies multiple categories.
    matches: list[ModelCategoryDef] = []
    model_matches: list[ModelCategoryDef] = []
    for category in categories.categories:
        presets = category.presets
        if any(p.model == selection.model for p in presets):
            model_matches.append(category)
        if any(
            p.model == selection.model
            and p.reasoning_effort == selection.reasoning_effort
            and (
                p.context_tier == selection.context_tier
                if agents
                else p.context_tokens == selection.context_tokens
            )
            for p in presets
        ):
            matches.append(category)
    if len(matches) == 1:
        return matches[0]
    if not matches and len(model_matches) == 1:
        return model_matches[0]
    # Never guess a category for an ambiguous model or uncategorised selection.
    return None


def category_presets(
    categories: ModelCategories,
    selection: ModelSelection | ModelPreset,
    *,
    agents: bool = False,
) -> list[ModelPreset]:
    found = matching_category(categories, selection, agents=agents)
    return list(found.presets) if found is not None else []


def is_model_rejection(error: BaseException | str) -> bool:
    """Only model availability / effort / context configuration failures."""
    cause = error.__cause__ if isinstance(error, BaseException) else None
    status = getattr(error, "status_code", None) or getattr(cause, "status_code", None)
    if status is not None and status not in (400, 404):
        return False
    text = str(error).lower()
    # These require user intervention, not a change of model.
    if any(
        marker in text
        for marker in (
            "credentials",
            "unauthorized",
            "authentication",
            "api key",
            "invalid_api_key",
            "rate_limit",
            "rate limit",
            "quota",
            "too many tools",
        )
    ):
        return False
    if any(
        code in text
        for code in (
            "invalid_reasoning_effort",
            "unsupported_reasoning_effort",
            "model_not_found",
            "model_not_available",
            "unknown_model",
            "invalid_model",
            "context_length_exceeded",
            "invalid_context_tier",
            "unsupported_context_tier",
            "kept refusing this model",
        )
    ):
        return True
    return bool(
        re.search(
            r"(?:model.{0,100}(?:not found|not available|unavailable|not supported|"
            r"does not exist|retired|deprecated|does not support reasoning)|"
            r"(?:unknown|unsupported|invalid) model|"
            r"(?:reasoning[_ ]effort|context[_ ]tier).{0,100}"
            r"(?:not supported|unsupported|invalid|does not support))",
            text,
        )
    )
