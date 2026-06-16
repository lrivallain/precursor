"""LLM catalog router — exposes available providers and the active provider's models."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.llm import LLMModelRead, ProviderFieldRead, ProviderRead
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.registry import PROVIDERS

router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger(__name__)


@router.get("/providers", response_model=list[ProviderRead])
async def list_providers() -> list[ProviderRead]:
    """Return the available LLM providers and their config field metadata."""
    return [
        ProviderRead(
            id=spec.id,
            label=spec.label,
            uses_github_token=spec.uses_github_token,
            discovers_models=spec.discovers_models,
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
        for spec in PROVIDERS.values()
    ]


@router.get("/models", response_model=list[LLMModelRead])
async def list_models(session: AsyncSession = Depends(get_session)) -> list[LLMModelRead]:
    provider = await get_llm_provider(session)
    lister = getattr(provider, "list_models", None)
    if lister is None:
        return []
    try:
        models = await lister()
    except Exception as exc:  # network / auth failures shouldn't 500 the UI
        logger.warning("Failed to fetch model catalog from %s: %s", provider.name, exc)
        raise HTTPException(status_code=502, detail=f"catalog fetch failed: {exc}") from exc
    return [LLMModelRead(**asdict(m)) for m in models]
