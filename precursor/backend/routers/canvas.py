"""Conversation canvases: the canvas workspace, a conversation's canvases, and
the state a canvas page saves through the SDK (``frontend/public/canvas-sdk.js``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.schemas.canvas import CanvasRead, CanvasState, CanvasWorkspace
from precursor.backend.services import canvases
from precursor.backend.services.canvases import CanvasError

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


def _bad_request(exc: CanvasError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/workspace", response_model=CanvasWorkspace)
async def canvas_workspace(session: AsyncSession = Depends(get_session)) -> CanvasWorkspace:
    ws = await canvases.ensure_canvas_workspace(session)
    return CanvasWorkspace(id=ws.id, slug=ws.slug)


@router.get("/{conversation_id}", response_model=list[CanvasRead])
async def list_canvases(conversation_id: str) -> list[CanvasRead]:
    try:
        found = canvases.list_canvases(conversation_id)
    except CanvasError as exc:
        raise _bad_request(exc) from exc
    return [
        CanvasRead(name=c.name, kind=c.kind, path=c.path, bytes=c.bytes, updated_at=c.updated_at)
        for c in found
    ]


@router.get("/{conversation_id}/{name}/state", response_model=CanvasState)
async def read_canvas_state(conversation_id: str, name: str) -> CanvasState:
    try:
        return CanvasState(state=canvases.read_state(conversation_id, name))
    except CanvasError as exc:
        raise _bad_request(exc) from exc


@router.put("/{conversation_id}/{name}/state", response_model=CanvasState)
async def write_canvas_state(conversation_id: str, name: str, payload: CanvasState) -> CanvasState:
    try:
        if canvases.find_canvas(conversation_id, name) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Canvas not found")
        canvases.write_state(conversation_id, name, payload.state)
    except CanvasError as exc:
        raise _bad_request(exc) from exc
    return payload
