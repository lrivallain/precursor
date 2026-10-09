"""Shared category matching and narrowly scoped rejection recovery."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.schemas.model_fallback import ModelCategories, ModelPreset

logger = logging.getLogger(__name__)
CATEGORY_NAMES = ("efficiency", "balanced", "intelligence")


@dataclass(frozen=True)
class ModelSelection:
    model: str
    reasoning_effort: str = ""
    context_tokens: int = 128_000
    context_tier: str = "default"


async def resolve_model_fallbacks(session: AsyncSession, scope: str) -> ModelCategories:
    from precursor.backend.services.app_settings import _get_db_value

    raw = await _get_db_value(session, "model_fallbacks")
    if not isinstance(raw, dict) or scope not in raw:
        return ModelCategories()
    try:
        return ModelCategories.model_validate(raw[scope])
    except ValidationError:
        logger.exception("Invalid saved model fallback configuration for %s", scope)
        return ModelCategories()


def category_presets(
    categories: ModelCategories,
    selection: ModelSelection | ModelPreset,
    *,
    agents: bool = False,
) -> list[ModelPreset]:
    # Exact configuration wins when the same model occupies multiple tiers.
    matches: list[list[ModelPreset]] = []
    model_matches: list[list[ModelPreset]] = []
    for name in CATEGORY_NAMES:
        presets: list[ModelPreset] = getattr(categories, name)
        if any(p.model == selection.model for p in presets):
            model_matches.append(presets)
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
            matches.append(presets)
    if len(matches) == 1:
        return matches[0]
    if not matches and len(model_matches) == 1:
        return model_matches[0]
    # Never guess a category for an ambiguous model or uncategorised selection.
    return []


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
