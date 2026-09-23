"""System slash commands accepted inside an agent session.

Each handler takes ``(manager, agent_id, argument)`` and reaches state and
sibling operations through the manager at call time (``manager.clear_session``
etc.), so patching the manager in tests still takes effect. ``AgentManager``
exposes this registry as ``_COMMAND_HANDLERS`` and keeps ``run_command`` /
``supported_commands``. Must not import ``manager`` at runtime.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from precursor.backend.db import SessionLocal
from precursor.backend.models import Role

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager


async def cmd_rename(manager: AgentManager, agent_id: int, argument: str) -> None:
    title = " ".join(argument.split())[:200]
    if not title:
        raise ValueError("Usage: /rename <new title>")
    await manager._patch(agent_id, title=title)
    await manager._publish(agent_id)


async def cmd_archive(manager: AgentManager, agent_id: int, argument: str) -> None:
    agent = await manager._load(agent_id)
    if agent is not None and agent.archived_at is None:
        await manager._patch(agent_id, archived_at=datetime.now(UTC))
        await manager._publish(agent_id)


async def cmd_clear(manager: AgentManager, agent_id: int, argument: str) -> None:
    await manager.clear_session(agent_id)


async def cmd_role(manager: AgentManager, agent_id: int, argument: str) -> None:
    name = " ".join(argument.split())
    if not name:
        # The bare form opens the composer's role picker client-side, so it
        # normally never reaches here.
        raise ValueError("Usage: /role <name>")
    async with SessionLocal() as session:
        role = (
            await session.execute(select(Role).where(func.lower(Role.name) == name.lower()))
        ).scalar_one_or_none()
    if role is None:
        raise ValueError(f'Unknown role "{name}". Manage roles in Settings → Roles.')
    # The default role is persisted as NULL, never by its own id.
    await manager._patch(agent_id, role_id=None if role.is_default else role.id)
    await manager._publish(agent_id)


async def cmd_memory_store(manager: AgentManager, agent_id: int, argument: str) -> None:
    from precursor.backend.services import memories as memory_service

    payload = memory_service.parse_store_arg(argument)
    async with SessionLocal() as session:
        await memory_service.create_memory(session, payload)


async def cmd_memory_update(manager: AgentManager, agent_id: int, argument: str) -> None:
    from precursor.backend.services import memories as memory_service

    memory_id, payload = memory_service.parse_update_arg(argument)
    async with SessionLocal() as session:
        try:
            await memory_service.update_memory(session, memory_id, payload)
        except LookupError as exc:
            raise ValueError(str(exc)) from exc


# Registry of system slash commands available inside an agent session:
# name -> async handler. The set of supported names (used for validation and
# the rejection message) is derived from these keys, and the frontend picker
# mirrors it via AGENT_SLASH_COMMANDS, so a new command is a single entry.
COMMAND_HANDLERS: dict[str, Callable[[AgentManager, int, str], Awaitable[None]]] = {
    "rename": cmd_rename,
    "archive": cmd_archive,
    "clear": cmd_clear,
    "role": cmd_role,
    "memory-store": cmd_memory_store,
    "memory-update": cmd_memory_update,
}
