"""Memory command helpers — parse slash-command arguments and persist entries.

The HTTP CRUD lives in ``routers/memories.py``; this module backs the *command*
surfaces that don't go through HTTP — the headless scheduled-topic runner
(``scheduled_commands.py``), the agent session manager, and the built-in MCP
write tools. Parsing is shared so ``/memory-store`` and ``/memory-update`` behave
identically wherever they're typed.

Argument grammar (the leading ``[kind]`` is optional; ``kind`` defaults to
``context``)::

    /memory-store  [kind] content...
    /memory-update <id> [kind] content...
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Memory
from precursor.backend.schemas import MemoryCreate, MemoryUpdate

_BRACKET_KIND_RE = re.compile(r"^\[([^\]]*)\]\s*([\s\S]*)$")

_MEMORY_PROMPT_HEADER = "Long-term user memory — treat as standing context for every turn:"


def _split_kind(argument: str) -> tuple[str | None, str]:
    """Peel an optional leading ``[kind]`` token off ``argument``.

    Returns ``(kind, rest)`` where ``kind`` is ``None`` when no bracket prefix is
    present. ``kind`` is returned verbatim (lowercasing/validation happens when
    the value flows through the Pydantic schema).
    """
    match = _BRACKET_KIND_RE.match(argument.strip())
    if match is None:
        return None, argument.strip()
    return match.group(1).strip(), match.group(2).strip()


def parse_store_arg(argument: str) -> MemoryCreate:
    """Parse a ``/memory-store`` argument into a validated :class:`MemoryCreate`.

    Raises :class:`ValueError` on empty content or an invalid kind.
    """
    kind, content = _split_kind(argument)
    if not content:
        raise ValueError("Usage: `/memory-store [kind] <content>`")
    return MemoryCreate(kind=kind or "context", content=content)


def parse_update_arg(argument: str) -> tuple[int, MemoryUpdate]:
    """Parse a ``/memory-update`` argument into ``(id, MemoryUpdate)``.

    Raises :class:`ValueError` when the id is missing/non-numeric or when no new
    content/kind is supplied.
    """
    head, _, tail = argument.strip().partition(" ")
    if not head:
        raise ValueError("Usage: `/memory-update <id> [kind] <content>`")
    try:
        memory_id = int(head)
    except ValueError as exc:
        raise ValueError("Usage: `/memory-update <id> [kind] <content>`") from exc
    kind, content = _split_kind(tail)
    if not content and kind is None:
        raise ValueError("Usage: `/memory-update <id> [kind] <content>`")
    payload = MemoryUpdate(
        kind=kind if kind else None,
        content=content if content else None,
    )
    return memory_id, payload


async def create_memory(session: AsyncSession, payload: MemoryCreate) -> Memory:
    """Persist a new memory and return it."""
    memory = Memory(**payload.model_dump())
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


async def update_memory(session: AsyncSession, memory_id: int, payload: MemoryUpdate) -> Memory:
    """Update an existing memory in place. Raises :class:`LookupError` if absent."""
    memory = await session.get(Memory, memory_id)
    if memory is None:
        raise LookupError(f"Memory {memory_id} not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(memory, key, value)
    await session.commit()
    await session.refresh(memory)
    return memory


async def build_memory_prompt(session: AsyncSession) -> str | None:
    """Render all memories as a system-prompt block, or ``None`` when there are none.

    Shared by every runtime that injects standing context — topic chats, flat
    chats, and agent sessions — so the wording stays identical everywhere.
    """
    rows = (
        (await session.execute(select(Memory).order_by(Memory.kind, Memory.created_at)))
        .scalars()
        .all()
    )
    if not rows:
        return None
    lines = [_MEMORY_PROMPT_HEADER]
    lines.extend(f"- [{m.kind.upper()}] {m.content.strip()}" for m in rows)
    return "\n".join(lines)


async def render_memory_list(session: AsyncSession) -> str:
    """Render memories as an id-bearing chat receipt (for ``/memory-list``)."""
    rows = (await session.execute(select(Memory).order_by(Memory.id))).scalars().all()
    if not rows:
        return "No memories yet. Add one with `/memory-store [kind] <content>`."

    def _cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")

    lines = [
        "**Long-term memories** — edit with `/memory-update <id> [kind] <content>`:",
        "",
        "| ID | Kind | Memory |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| #{m.id} | {_cell(m.kind)} | {_cell(m.content)} |" for m in rows)
    return "\n".join(lines)
