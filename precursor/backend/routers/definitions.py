"""Definitions folder API — the integrity check and the one-shot export.

Work in progress (see docs/definitions.md): nothing at runtime reads the files
yet, so both routes only report on, or write, the folder.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import get_session
from precursor.backend.schemas.definitions_api import (
    DefinitionsCheckReport,
    DefinitionsExportResult,
)
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.definitions.exporter import export_definitions
from precursor.backend.services.definitions.service import check_folder

router = APIRouter(prefix="/api/definitions", tags=["definitions"])


@router.get("/check", response_model=DefinitionsCheckReport)
async def check_definitions(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> DefinitionsCheckReport:
    return await check_folder(session, Path(settings.definitions_dir))


@router.post("/export", response_model=DefinitionsExportResult)
async def export_to_files(
    overwrite: bool = False,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> DefinitionsExportResult:
    """Write a file for every agent and workflow that doesn't have one yet.

    ``overwrite`` also rewrites the files of rows that already have one, from
    the database — which is still authoritative at this stage.
    """
    if not await resolve_agents_enabled(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agents mode is disabled")
    if overwrite and settings.definitions_source == "files":
        # The files are the source now: regenerating them from the database
        # would overwrite them with the stale copy.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Definitions are read from files; overwriting them from the database is disabled",
        )
    return await export_definitions(session, Path(settings.definitions_dir), overwrite=overwrite)
