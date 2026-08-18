"""Resolve the system prompt contributed by a discussion's Assistant Role."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Role
from precursor.backend.services import skills as skills_service


async def resolve_role_prompt(session: AsyncSession, role_id: int | None) -> str:
    """Return the system prompt to inject for ``role_id``.

    A null ``role_id`` (or one pointing at a since-deleted role) resolves to the
    built-in ``default`` role, whose prompt is empty out of the box — so the net
    effect is "inject nothing" unless the user customised it. Returns a stripped
    string, or "" when there is nothing to inject.

    A role prompt may reference skills (``/rewrite``), expanded here so every
    surface that adopts a role — topics, chats, workspaces, agents, meetings —
    gets the same treatment.
    """
    role: Role | None = None
    if role_id is not None:
        role = await session.get(Role, role_id)
    if role is None:
        result = await session.execute(select(Role).where(Role.is_default.is_(True)))
        role = result.scalars().first()
    if role is None:
        return ""
    return (await skills_service.expand_references(session, role.system_prompt)).strip()
