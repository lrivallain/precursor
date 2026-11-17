"""Conversation canvas tools: draw visuals a conversation shows inline.

Every canvas lives in the built-in ``canvas`` workspace under the
conversation's folder (``services/canvases.py``). A write returns the workspace
link, so the turn engine attaches the canvas to the conversation as an
artifact, and the ``embed`` block the reply pastes to show it.
"""

from __future__ import annotations

from typing import Any, Literal

from precursor.backend.config import CANVAS_WORKSPACE_SLUG
from precursor.backend.db import SessionLocal
from precursor.backend.services import canvases, skills
from precursor.backend.services.canvases import CanvasError
from precursor.backend.services.mcp.precursor_tools._core import _GATED, _section_enabled, mcp
from precursor.backend.services.mcp.workspace_links import with_open_link

_SECTION = "canvas"


async def _gate() -> dict[str, Any] | None:
    if not await _section_enabled(_SECTION):
        return {"error": _GATED.format(section=_SECTION)}
    return None


async def _conversation(conversation_id: str) -> str:
    conversation_id = canvases.check_conversation_id(conversation_id)
    async with SessionLocal() as session:
        if not await canvases.conversation_exists(session, conversation_id):
            raise CanvasError(f"No conversation with id {conversation_id}.")
        await canvases.ensure_canvas_workspace(session)
    return conversation_id


def _written(info: canvases.CanvasInfo, created: bool) -> dict[str, Any]:
    result = {**info.as_dict(), "written": True, "created": created}
    return with_open_link(result, CANVAS_WORKSPACE_SLUG, info.path)


@mcp.tool()
async def canvas_guide() -> dict[str, Any]:
    """How to make a good canvas: which kind to pick, the rules each kind follows,
    and the `window.precursor` SDK an HTML canvas can use. Read it once before
    your first canvas in a conversation."""
    if (gated := await _gate()) is not None:
        return gated
    return {"guide": skills.builtin_instructions("canvas")}


@mcp.tool()
async def list_canvases(conversation_id: str) -> dict[str, Any]:
    """List a conversation's canvases (name, kind, path, size, embed block)."""
    if (gated := await _gate()) is not None:
        return gated
    try:
        conversation_id = canvases.check_conversation_id(conversation_id)
        return {"canvases": [c.as_dict() for c in canvases.list_canvases(conversation_id)]}
    except CanvasError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def read_canvas(conversation_id: str, name: str) -> dict[str, Any]:
    """Read one canvas's source, to change it with edit_canvas or write_canvas."""
    if (gated := await _gate()) is not None:
        return gated
    try:
        info = canvases.find_canvas(canvases.check_conversation_id(conversation_id), name)
        if info is None:
            return {"error": f"No canvas named {name!r} in this conversation."}
        return {**info.as_dict(), "content": canvases.read_canvas(info)}
    except CanvasError as exc:
        return {"error": str(exc)}


@mcp.tool()
async def write_canvas(
    conversation_id: str,
    name: str,
    kind: Literal["html", "drawio", "mermaid", "svg"],
    content: str,
) -> dict[str, Any]:
    """Create or replace a canvas in this conversation.

    kind: "html" (a self-contained interactive page: simulations, animations,
    charts, explorable explanations), "drawio" (draw.io / mxGraph XML for
    architecture diagrams the user can edit), "mermaid" (flowcharts, sequences,
    states, timelines) or "svg" (a static illustration). ``name`` is a short
    label; the same name replaces the canvas. Paste the returned ``embed``
    block into your reply to show it.
    """
    if (gated := await _gate()) is not None:
        return gated
    try:
        conversation_id = await _conversation(conversation_id)
        created = canvases.find_canvas(conversation_id, name) is None
        info = canvases.write_canvas(conversation_id, name, kind, content)
    except CanvasError as exc:
        return {"error": str(exc)}
    return _written(info, created)


@mcp.tool()
async def edit_canvas(
    conversation_id: str, name: str, edits: list[dict[str, str]]
) -> dict[str, Any]:
    """Change part of a canvas without rewriting it.

    ``edits`` is a list of ``{"find": ..., "replace": ...}`` applied in order;
    each ``find`` must match exactly once in the current source (read_canvas).
    """
    if (gated := await _gate()) is not None:
        return gated
    try:
        conversation_id = await _conversation(conversation_id)
        info = canvases.find_canvas(conversation_id, name)
        if info is None:
            return {"error": f"No canvas named {name!r} in this conversation."}
        content = canvases.apply_edits(canvases.read_canvas(info), edits)
        info = canvases.write_canvas(conversation_id, info.name, info.kind, content)
    except CanvasError as exc:
        return {"error": str(exc)}
    return _written(info, False)
