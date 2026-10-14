"""Resolve explicit conversation preferences without changing app defaults."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Chat, Topic
from precursor.backend.schemas.conversation_llm import ConversationLLMSelection
from precursor.backend.services.llm import ProviderConfigurationError, get_llm_provider
from precursor.backend.services.llm.base import LLMProvider
from precursor.backend.services.model_catalog import offered_model_ids
from precursor.backend.services.model_fallbacks import (
    ModelSelection,
    category_presets,
    resolve_model_fallbacks,
    selected_category_presets,
)

logger = logging.getLogger(__name__)


async def validate_selection(
    session: AsyncSession, selection: ConversationLLMSelection | None
) -> None:
    if selection is not None and selection.category is not None:
        categories = await resolve_model_fallbacks(session)
        if not selected_category_presets(categories, selection.category):
            raise ProviderConfigurationError(
                f"No presets are configured for the {selection.category!r} category."
            )


async def saved_selection(
    session: AsyncSession, *, topic_id: int | None = None, chat_id: int | None = None
) -> ConversationLLMSelection | None:
    container: Chat | Topic | None = None
    if topic_id is not None:
        container = await session.get(Topic, topic_id)
    elif chat_id is not None:
        container = await session.get(Chat, chat_id)
    if container is None or container.llm_selection is None:
        return None
    return ConversationLLMSelection.model_validate_json(container.llm_selection)


@dataclass(frozen=True)
class ResolvedConversationLLM:
    provider: LLMProvider
    model: str
    reasoning_effort: str
    context_tokens: int


async def resolve_selection(
    session: AsyncSession,
    selection: ConversationLLMSelection,
    *,
    model_override: str | None = None,
) -> ResolvedConversationLLM:
    category = selection.category if not model_override else None
    categories = await resolve_model_fallbacks(
        session, "llm" if category or not selection.provider else selection.provider
    )
    presets = selected_category_presets(categories, category) if category else []
    if category and not presets:
        raise ProviderConfigurationError(
            f"No presets are configured for the {category!r} category. "
            "Choose a model or configure presets in Settings > Presets."
        )
    budget = max(p.context_tokens for p in presets) if presets else selection.context_tokens
    provider = await get_llm_provider(
        session,
        override_provider=selection.provider or None,
        model_category=category,
        strict=bool(selection.provider),
        context_tokens=budget,
    )
    model = model_override or selection.model or presets[0].model
    if not category and not model_override:
        offered = await offered_model_ids(session, selection.provider)
        desired = ModelSelection(
            model=model,
            reasoning_effort=selection.reasoning_effort,
            context_tokens=selection.context_tokens,
        )
        if offered and model not in offered and not category_presets(categories, desired):
            logger.warning(
                "Conversation model %r retired from %s; using %r",
                model,
                selection.provider,
                offered[0],
            )
            model = offered[0]
    return ResolvedConversationLLM(provider, model, selection.reasoning_effort, budget)
