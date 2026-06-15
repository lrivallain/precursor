"""Raw workspace file serving — static-web-server-like access to files.

Serves files from a workspace's working tree by slug at ``/raw/<slug>/<path>``
so they can be opened directly in a browser (HTML renders, images display,
text shows) and relative links inside a file resolve naturally. Read-only and
unauthenticated by design (local single-user app).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.routers.workspaces import browse_root, get_workspace_by_slug
from precursor.backend.services import workspace_fs as fs

router = APIRouter(prefix="/raw", tags=["raw"])


@router.get("/{slug}/{file_path:path}")
async def raw_file(
    slug: str,
    file_path: str,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    ws = await get_workspace_by_slug(slug, session)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    try:
        target = fs.safe_join(browse_root(ws), file_path)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FileResponse(target)
