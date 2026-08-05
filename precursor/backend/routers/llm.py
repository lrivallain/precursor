"""LLM catalog router — exposes available providers and the active provider's models."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.llm import LLMModelRead, ProviderFieldRead, ProviderRead
from precursor.backend.services.app_settings import resolve_llm_provider
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.registry import PROVIDERS, selectable_providers

router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger(__name__)


@router.get("/providers", response_model=list[ProviderRead])
async def list_providers(session: AsyncSession = Depends(get_session)) -> list[ProviderRead]:
    """Return the selectable LLM providers and their config field metadata.

    Retired providers are omitted unless one is the active selection, so a user
    already pointed at a dead service still sees it (flagged) instead of a
    picker whose value has vanished.
    """
    active = await resolve_llm_provider(session)
    return [
        ProviderRead(
            id=spec.id,
            label=spec.label,
            uses_github_token=spec.uses_github_token,
            discovers_models=spec.discovers_models,
            retired=spec.retired,
            fields=[
                ProviderFieldRead(
                    name=f.name,
                    label=f.label,
                    secret=f.secret,
                    required=f.required,
                    placeholder=f.placeholder,
                    help=f.help,
                )
                for f in spec.fields
            ],
        )
        for spec in selectable_providers(active)
    ]


@router.get("/models", response_model=list[LLMModelRead])
async def list_models(
    provider: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[LLMModelRead]:
    # Retired upstreams can't answer; say so plainly instead of letting the
    # transport error ("Client error '410 Gone' for url …") reach the UI.
    requested = (
        provider if provider and provider in PROVIDERS else await resolve_llm_provider(session)
    )
    spec = PROVIDERS.get(requested)
    if spec is not None and spec.retired:
        raise HTTPException(status_code=502, detail=spec.retired)
    llm = await get_llm_provider(session, override_provider=provider)
    lister = getattr(llm, "list_models", None)
    if lister is None:
        return []
    try:
        models = await lister()
    except Exception as exc:  # network / auth failures shouldn't 500 the UI
        logger.warning("Failed to fetch model catalog from %s: %s", llm.name, exc)
        raise HTTPException(status_code=502, detail=f"catalog fetch failed: {exc}") from exc
    return [LLMModelRead(**asdict(m)) for m in models]
