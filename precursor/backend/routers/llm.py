"""LLM catalog router — exposes the configured provider's selectable models."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from precursor.backend.schemas.llm import LLMModelRead
from precursor.backend.services.llm import get_llm_provider

router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = logging.getLogger(__name__)


@router.get("/models", response_model=list[LLMModelRead])
async def list_models() -> list[LLMModelRead]:
    provider = get_llm_provider()
    lister = getattr(provider, "list_models", None)
    if lister is None:
        return []
    try:
        models = await lister()
    except Exception as exc:  # network / auth failures shouldn't 500 the UI
        logger.warning("Failed to fetch model catalog from %s: %s", provider.name, exc)
        raise HTTPException(status_code=502, detail=f"catalog fetch failed: {exc}") from exc
    return [LLMModelRead(**asdict(m)) for m in models]
