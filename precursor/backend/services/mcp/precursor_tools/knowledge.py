"""Skills and long-term memory tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    Memory,
)

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import _GATED, _section_enabled, mcp


# --------------------------------------------------------------------------
# skills
# --------------------------------------------------------------------------
@mcp.tool()
async def list_skills() -> dict[str, Any]:
    """List Precursor skills (name, description, active) — reusable prompt presets."""
    if not await _section_enabled("skills"):
        return {"error": _GATED.format(section="skills")}
    from precursor.backend.services import skills as skills_service

    async with SessionLocal() as session:
        resolved = await skills_service.reconcile_and_list(session)
    return {
        "skills": [
            {"name": s.name, "description": s.description, "active": s.active} for s in resolved
        ],
        "count": len(resolved),
    }


@mcp.tool()
async def get_skill(name: str) -> dict[str, Any]:
    """Get a skill by name, including its full instructions text."""
    if not await _section_enabled("skills"):
        return {"error": _GATED.format(section="skills")}
    from precursor.backend.services import skills as skills_service

    async with SessionLocal() as session:
        s = await skills_service.get_resolved(session, name)
    if s is None:
        return {"error": f"Skill '{name}' not found"}
    return {
        "name": s.name,
        "description": s.description,
        "instructions": s.instructions,
        "active": s.active,
    }


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------
@mcp.tool()
async def list_memories() -> dict[str, Any]:
    """List Precursor's long-term memory entries (id, kind, content)."""
    if not await _section_enabled("memory"):
        return {"error": _GATED.format(section="memory")}
    async with SessionLocal() as session:
        rows = (await session.execute(select(Memory).order_by(Memory.id))).scalars().all()
    return {
        "memories": [{"id": m.id, "kind": m.kind, "content": m.content} for m in rows],
        "count": len(rows),
    }


@mcp.tool()
async def store_memory(content: str, kind: str = "context") -> dict[str, Any]:
    """Save a new long-term memory injected into every future conversation.

    ``content`` is the standing fact/preference/context to remember; ``kind`` is a
    short lowercase tag (e.g. "context", "preference", "fact") shown in the UI and
    prepended to the line sent to the model. Returns the created entry.
    """
    if not await _section_enabled("memory_write"):
        return {"error": _GATED.format(section="memory_write")}
    from precursor.backend.schemas import MemoryCreate
    from precursor.backend.services import memories as memory_service

    try:
        payload = MemoryCreate(kind=kind, content=content)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        memory = await memory_service.create_memory(session, payload)
        return {"id": memory.id, "kind": memory.kind, "content": memory.content}


@mcp.tool()
async def update_memory(
    memory_id: int, content: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    """Edit an existing long-term memory by id (from ``list_memories``).

    Pass ``content`` and/or ``kind`` to change; omitted fields are left as-is.
    Returns the updated entry, or an error if the id is unknown.
    """
    if not await _section_enabled("memory_write"):
        return {"error": _GATED.format(section="memory_write")}
    from precursor.backend.schemas import MemoryUpdate
    from precursor.backend.services import memories as memory_service

    if content is None and kind is None:
        return {"error": "Provide content and/or kind to update"}
    try:
        payload = MemoryUpdate(content=content, kind=kind)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        try:
            memory = await memory_service.update_memory(session, memory_id, payload)
        except LookupError:
            return {"error": f"Memory {memory_id} not found"}
        return {"id": memory.id, "kind": memory.kind, "content": memory.content}
