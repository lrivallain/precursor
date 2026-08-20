"""draw.io editor assets — install status plus static hosting of the webapp.

The API side reports/triggers the on-demand install; the asset side serves the
extracted webapp at ``/drawio/*`` so the editor iframe loads entirely from this
origin (no call to ``embed.diagrams.net``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status
from fastapi.responses import FileResponse

from precursor.backend.schemas.drawio import DrawioStatus
from precursor.backend.services import drawio as service

router = APIRouter(prefix="/api/drawio", tags=["drawio"])
assets_router = APIRouter(prefix="/drawio", tags=["drawio"])


@router.get("/status", response_model=DrawioStatus)
async def get_status() -> DrawioStatus:
    return service.status()


@router.post("/install", response_model=DrawioStatus)
async def install() -> DrawioStatus:
    return service.start_install()


@assets_router.get("/{file_path:path}", include_in_schema=False)
async def asset(file_path: str) -> FileResponse:
    target = service.asset_path(file_path)
    if target is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "draw.io asset not found")
    return FileResponse(target)
