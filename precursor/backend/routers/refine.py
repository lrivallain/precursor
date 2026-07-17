"""Refine with AI router — rewrite a block of user text on demand."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.refine import RefineRequest, RefineResponse
from precursor.backend.services.text_refine import refine_text

router = APIRouter(prefix="/api/refine", tags=["refine"])
logger = logging.getLogger(__name__)


@router.post("", response_model=RefineResponse)
async def refine(
    payload: RefineRequest,
    session: AsyncSession = Depends(get_session),
) -> RefineResponse:
    if not payload.text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to refine.")
    try:
        text, model = await refine_text(
            session,
            text=payload.text,
            kind=payload.kind,
            instruction=payload.instruction,
        )
    except Exception as exc:  # provider / network failures shouldn't 500 the UI
        logger.warning("Refine failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Refine failed: {exc}") from exc
    return RefineResponse(text=text, model=model)
