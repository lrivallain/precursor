"""Fleet concurrency governor for the agent runtime.

This module holds the *pure* orchestration query the manager needs to keep the
agent pool bounded (no SDK, no live sessions). Agent-to-agent chaining now lives
in the Workflows feature, so the only primitive left here is the concurrency
governor: the manager auto-releases/retries agents while the fleet is under
``settings.agents_max_concurrent`` so a burst of queued work drains as a bounded
queue instead of stampeding the host or the model rate limit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from precursor.backend.models.agent_session import AgentSession

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Statuses that count as "occupying a concurrency slot" — an agent actively
# working a turn or parked mid-turn on a tool gate. Waiting/terminal states
# (pending/idle/blocked/completed/...) don't consume the turn budget.
BUSY_STATUSES = ("running", "needs_approval")


async def running_count(session: AsyncSession) -> int:
    """How many agents currently occupy a concurrency slot (running-ish)."""
    row = await session.execute(
        select(func.count()).select_from(AgentSession).where(AgentSession.status.in_(BUSY_STATUSES))
    )
    return int(row.scalar_one() or 0)
