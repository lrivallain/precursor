"""Retention for the archived agent timeline (``agent_events``).

Every normalised SDK event an agent emits is archived so the workflow timeline
survives a restart (see ``models/agent_event.py``). Nothing bounded that table,
so it becomes the single largest object in a busy install — one prod DB held
70 MB of events against 17 MB of actual messages.

Two independent levers are applied, because agent traffic is **bursty rather
than aged**: a handful of long autonomous sessions can add tens of MB in a day,
which a pure time window would not touch for weeks.

* ``agent_event_retention_days`` — delete events older than the window.
* ``agent_event_max_per_session`` — keep only the newest N events per agent.

Both skip agents that are currently live: the in-memory timeline is rehydrated
from these rows on restart (``manager._ensure_loaded``), so pruning a running
session would truncate the trace a user is actively watching. Terminal and
archived agents keep their ``result_summary`` and artifacts either way — only
the verbose event trace is dropped.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentEventRecord, AgentSession
from precursor.backend.services.app_settings import (
    resolve_agent_event_max_per_session,
    resolve_agent_event_retention_days,
)
from precursor.backend.services.sweep_result import SweepResult

logger = logging.getLogger(__name__)

# Statuses whose event trace is still in use — a live timeline is being streamed
# to the UI and rebuilt from these rows after a restart.
LIVE_STATUSES = ("pending", "running", "needs_approval")


def _prunable_agents() -> Any:
    """Subquery of agent ids whose archived events are safe to prune."""
    return select(AgentSession.id).where(AgentSession.status.not_in(LIVE_STATUSES))


async def _expired_ids(session: AsyncSession, retention_days: int) -> list[int]:
    """Ids of events past the retention window, for prunable agents."""
    if retention_days <= 0:
        return []
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    rows = await session.execute(
        select(AgentEventRecord.id).where(
            AgentEventRecord.created_at < cutoff,
            AgentEventRecord.agent_session_id.in_(_prunable_agents()),
        )
    )
    return [int(r) for r in rows.scalars()]


async def _overflow_ids(session: AsyncSession, max_per_session: int) -> list[int]:
    """Ids beyond the newest ``max_per_session`` events of each prunable agent.

    Ordered by ``id`` rather than ``created_at``: it is monotonic per session and
    unique, so the newest N is unambiguous even for events archived in the same
    clock tick.
    """
    if max_per_session <= 0:
        return []
    ranked = (
        select(
            AgentEventRecord.id.label("id"),
            func.row_number()
            .over(
                partition_by=AgentEventRecord.agent_session_id,
                order_by=AgentEventRecord.id.desc(),
            )
            .label("rn"),
        )
        .where(AgentEventRecord.agent_session_id.in_(_prunable_agents()))
        .subquery()
    )
    rows = await session.execute(select(ranked.c.id).where(ranked.c.rn > max_per_session))
    return [int(r) for r in rows.scalars()]


async def _measure(session: AsyncSession, ids: list[int]) -> SweepResult:
    """Row count and payload bytes for ``ids``, without deleting anything."""
    if not ids:
        return SweepResult()
    total = await session.scalar(
        select(func.coalesce(func.sum(func.length(AgentEventRecord.payload)), 0)).where(
            AgentEventRecord.id.in_(ids)
        )
    )
    return SweepResult(rows=len(ids), bytes=int(total or 0))


async def prune_agent_events(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] = SessionLocal,
    *,
    dry_run: bool = False,
) -> SweepResult:
    """Apply both retention levers; return what was (or would be) removed.

    A no-op when both levers are disabled. With ``dry_run`` the rows are only
    measured, so the settings UI can preview a sweep before committing to it.
    """
    async with session_factory() as session:
        retention_days = await resolve_agent_event_retention_days(session)
        max_per_session = await resolve_agent_event_max_per_session(session)
        if retention_days <= 0 and max_per_session <= 0:
            return SweepResult()

        # Union the two levers so an event caught by both is counted once.
        ids = sorted(
            set(await _expired_ids(session, retention_days))
            | set(await _overflow_ids(session, max_per_session))
        )
        if not ids:
            return SweepResult()

        measured = await _measure(session, ids)
        if dry_run:
            return measured

        deleted = 0
        # Chunked to stay clear of the SQLite host-parameter ceiling (999 by
        # default) on installs with a large backlog.
        for start in range(0, len(ids), 500):
            result = await session.execute(
                delete(AgentEventRecord).where(AgentEventRecord.id.in_(ids[start : start + 500]))
            )
            deleted += int(cast("CursorResult[Any]", result).rowcount or 0)
        await session.commit()
        logger.info(
            "Pruned %d archived agent event(s) (~%d bytes; window=%dd, cap=%d/session)",
            deleted,
            measured.bytes,
            retention_days,
            max_per_session,
        )
        return SweepResult(rows=deleted, bytes=measured.bytes)
