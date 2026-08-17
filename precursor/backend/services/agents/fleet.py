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

from precursor.backend.models.agent_run import AgentRun

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Statuses that count as "occupying a concurrency slot" — actively working a turn
# or parked mid-turn on a tool gate. Waiting/terminal states
# (pending/idle/blocked/completed/...) don't consume the turn budget.
BUSY_STATUSES = ("running", "needs_approval")


async def running_count(session: AsyncSession) -> int:
    """How many executions currently occupy a concurrency slot (running-ish).

    Counted over ``agent_runs``, not agents: a slot exists to bound the number of
    live Copilot SDK sessions, and there is one session per **run**. Counting
    agents would let two workflows driving the same definition burn two sessions
    while charging the governor for one.
    """
    row = await session.execute(
        select(func.count()).select_from(AgentRun).where(AgentRun.status.in_(BUSY_STATUSES))
    )
    return int(row.scalar_one() or 0)
