"""Usage-statistics and storage-cockpit endpoints for the settings UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.stats import (
    CleanupPreview,
    CleanupRunResult,
    CleanupTargetStat,
    CompactResult,
    SystemStats,
    UsageStats,
)
from precursor.backend.services.storage_cleanup import (
    TARGETS,
    compact_database,
    get_target,
    preview_all,
    run_target,
)
from precursor.backend.services.system_stats import compute_system_stats
from precursor.backend.services.usage_stats import compute_usage_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/usage", response_model=UsageStats)
async def get_usage_stats(session: AsyncSession = Depends(get_session)) -> UsageStats:
    return await compute_usage_stats(session)


@router.get("/system", response_model=SystemStats)
async def get_system_stats(session: AsyncSession = Depends(get_session)) -> SystemStats:
    return await compute_system_stats(session)


@router.get("/cleanup", response_model=CleanupPreview)
async def get_cleanup_preview() -> CleanupPreview:
    """What each cleanup target would free right now — nothing is deleted."""
    measured = await preview_all()
    targets = [
        CleanupTargetStat(
            key=t.key,
            label=t.label,
            description=t.description,
            setting=t.setting,
            table=t.table,
            rows=measured[t.key].rows,
            bytes=measured[t.key].bytes,
        )
        for t in TARGETS
    ]
    return CleanupPreview(targets=targets, total_bytes=sum(t.bytes for t in targets))


@router.post("/cleanup/{key}", response_model=CleanupRunResult)
async def run_cleanup(key: str) -> CleanupRunResult:
    """Run one cleanup target now, ahead of its daily ticker."""
    if get_target(key) is None:
        raise HTTPException(status_code=404, detail=f"Unknown cleanup target: {key}")
    result = await run_target(key)
    return CleanupRunResult(key=key, rows=result.rows, bytes=result.bytes)


@router.post("/compact", response_model=CompactResult)
async def compact() -> CompactResult:
    """Return freed pages to the filesystem (``VACUUM``).

    Deleting rows alone never shrinks the file — ``auto_vacuum`` is off — so this
    is the step that makes a cleanup visible on disk.
    """
    result = await compact_database()
    return CompactResult(
        supported=result.supported,
        size_before=result.size_before,
        size_after=result.size_after,
        reclaimed_bytes=result.reclaimed,
        error=result.error,
    )
