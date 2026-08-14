"""Agent state service — read/upsert/delete an agent's cross-run scratchpad.

Shared by every surface that touches state so the guardrails (key cap, upsert
semantics, size limits) hold identically wherever a write comes from: the HTTP
router for the UI panel, and the built-in MCP ``state_*`` tools the agent itself
calls.

See :mod:`precursor.backend.models.agent_state` for why this is a separate
surface from memories and artifacts.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AgentState
from precursor.backend.models.agent_state import AGENT_STATE_MAX_KEYS
from precursor.backend.schemas.agent_state import AgentStateSummary, AgentStateWrite

_STATE_PROMPT_HEADER = (
    "Durable state you saved on a previous run (private to you, survives re-runs). "
    "Bodies are NOT included here — read one with the `state_get` tool when you "
    "need it, and save progress with `state_set` before you finish:"
)


async def list_states(session: AsyncSession, agent_id: int) -> list[AgentState]:
    """Every state entry for ``agent_id``, key-ordered."""
    rows = await session.execute(
        select(AgentState).where(AgentState.agent_id == agent_id).order_by(AgentState.key)
    )
    return list(rows.scalars().all())


async def list_state_keys(session: AsyncSession, agent_id: int) -> list[AgentStateSummary]:
    """The key index — keys, body sizes and mtimes, without the bodies.

    Deliberately projected in SQL rather than loaded and trimmed in Python: the
    point of the index is that a 100 KB body never reaches the caller.
    """
    rows = await session.execute(
        select(AgentState.key, func.length(AgentState.value), AgentState.updated_at)
        .where(AgentState.agent_id == agent_id)
        .order_by(AgentState.key)
    )
    return [
        AgentStateSummary(key=key, size=size or 0, updated_at=updated_at)
        for key, size, updated_at in rows.all()
    ]


async def get_state(session: AsyncSession, agent_id: int, key: str) -> AgentState | None:
    """One entry, or ``None`` when the agent never stored that key."""
    row = await session.execute(
        select(AgentState).where(AgentState.agent_id == agent_id, AgentState.key == key)
    )
    return row.scalar_one_or_none()


async def set_state(
    session: AsyncSession, agent_id: int, payload: AgentStateWrite
) -> tuple[AgentState, bool]:
    """Upsert ``payload`` for ``agent_id``; returns ``(row, created)``.

    Raises :class:`ValueError` when a *new* key would push the agent past
    :data:`AGENT_STATE_MAX_KEYS`. Overwriting an existing key is always allowed,
    so an agent that has hit the cap can still make progress on the keys it owns
    instead of being wedged.
    """
    existing = await get_state(session, agent_id, payload.key)
    if existing is not None:
        existing.value = payload.value
        await session.commit()
        await session.refresh(existing)
        return existing, False

    count = await session.scalar(
        select(func.count(AgentState.id)).where(AgentState.agent_id == agent_id)
    )
    if (count or 0) >= AGENT_STATE_MAX_KEYS:
        raise ValueError(
            f"Agent state is limited to {AGENT_STATE_MAX_KEYS} keys; "
            "delete an unused key before adding a new one."
        )
    state = AgentState(agent_id=agent_id, key=payload.key, value=payload.value)
    session.add(state)
    await session.commit()
    await session.refresh(state)
    return state, True


async def delete_state(session: AsyncSession, agent_id: int, key: str) -> bool:
    """Drop one entry. Returns ``False`` when the key wasn't there."""
    state = await get_state(session, agent_id, key)
    if state is None:
        return False
    await session.delete(state)
    await session.commit()
    return True


async def clear_states(session: AsyncSession, agent_id: int) -> int:
    """Drop every entry for an agent (the UI's "reset state"). Returns the count."""
    removed = await session.scalar(
        select(func.count(AgentState.id)).where(AgentState.agent_id == agent_id)
    )
    await session.execute(delete(AgentState).where(AgentState.agent_id == agent_id))
    await session.commit()
    return int(removed or 0)


async def build_state_index_prompt(session: AsyncSession, agent_id: int) -> str | None:
    """Render the key index as a system-prompt block, or ``None`` when empty.

    **Only keys** — never values. A recurring agent needs to *know* it has a
    cursor from last time; loading every body into the preamble would rebuild
    the context bloat that keeps this out of ``Memory`` in the first place.
    """
    entries = await list_state_keys(session, agent_id)
    if not entries:
        return None
    lines = [_STATE_PROMPT_HEADER]
    lines.extend(
        f"- `{e.key}` ({e.size} chars, updated {e.updated_at:%Y-%m-%d %H:%M} UTC)" for e in entries
    )
    return "\n".join(lines)
