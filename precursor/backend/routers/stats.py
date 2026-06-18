"""Usage-statistics endpoint — global token consumption for the settings UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.stats import UsageStats
from precursor.backend.services.usage_stats import compute_usage_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/usage", response_model=UsageStats)
async def get_usage_stats(session: AsyncSession = Depends(get_session)) -> UsageStats:
    return await compute_usage_stats(session)
