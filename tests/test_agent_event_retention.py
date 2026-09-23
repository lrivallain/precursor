"""Tests for retention of the archived agent timeline (``agent_events``).

The sweep applies two independent levers — an age window and a per-agent event
ceiling — and must never touch an agent that is still live, because the
in-memory timeline is rehydrated from these rows after a restart. Covers: both
levers disabled is a no-op; the age window prunes only expired events; the cap
keeps the newest N per agent; live agents are exempt from both; ``dry_run``
measures without deleting; and a backlog above SQLite's host-parameter ceiling
is swept in bounded statements.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, insert, select

from precursor.backend.db import SessionLocal, engine
from precursor.backend.main import create_app
from precursor.backend.models import AgentEventRecord, AgentSession, AppSetting
from precursor.backend.services.agent_event_retention import prune_agent_events


def _init_db() -> None:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass


async def _set(key: str, value: int) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, key)
        encoded = json.dumps(value)
        if row is None:
            session.add(AppSetting(key=key, value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def _configure(*, days: int, cap: int) -> None:
    await _set("agent_event_retention_days", days)
    await _set("agent_event_max_per_session", cap)


async def _seed() -> dict[str, int]:
    """One completed agent (old + fresh events) and one still-running agent."""
    old = datetime.now(UTC) - timedelta(days=40)
    fresh = datetime.now(UTC) - timedelta(hours=1)
    async with SessionLocal() as session:
        # Isolate from other tests sharing the session-wide temp DB.
        await session.execute(delete(AgentEventRecord))
        await session.execute(delete(AgentSession))

        done = AgentSession(title="Done", task_prompt="x", status="completed")
        live = AgentSession(title="Live", task_prompt="x", status="running")
        session.add_all([done, live])
        await session.flush()
        ids = {"done": done.id, "live": live.id}
        for agent_id in (done.id, live.id):
            session.add_all(
                [
                    AgentEventRecord(
                        agent_session_id=agent_id, payload='{"kind":"old"}', created_at=old
                    )
                    for _ in range(3)
                ]
            )
            session.add_all(
                [
                    AgentEventRecord(
                        agent_session_id=agent_id, payload='{"kind":"new"}', created_at=fresh
                    )
                    for _ in range(2)
                ]
            )
        await session.commit()
    return ids


async def _event_count(agent_id: int) -> int:
    async with SessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(AgentEventRecord)
                    .where(AgentEventRecord.agent_session_id == agent_id)
                )
            ).scalar_one()
        )


def test_both_levers_disabled_is_noop() -> None:
    _init_db()

    async def _run() -> None:
        await _configure(days=0, cap=0)
        ids = await _seed()
        assert (await prune_agent_events()).rows == 0
        assert await _event_count(ids["done"]) == 5

    asyncio.run(_run())


def test_age_window_prunes_only_expired_events_of_idle_agents() -> None:
    _init_db()

    async def _run() -> None:
        await _configure(days=30, cap=0)
        ids = await _seed()

        assert (await prune_agent_events()).rows == 3

        # The completed agent keeps only its two fresh events...
        assert await _event_count(ids["done"]) == 2
        # ...while the running agent is exempt: its live timeline is rebuilt
        # from these rows and must not be truncated mid-flight.
        assert await _event_count(ids["live"]) == 5

    asyncio.run(_run())


def test_per_session_cap_keeps_newest_events() -> None:
    _init_db()

    async def _run() -> None:
        await _configure(days=0, cap=2)
        ids = await _seed()

        assert (await prune_agent_events()).rows == 3

        assert await _event_count(ids["done"]) == 2
        assert await _event_count(ids["live"]) == 5
        # The survivors are the newest — the cap trims the tail, not the head.
        async with SessionLocal() as session:
            kinds = (
                (
                    await session.execute(
                        select(AgentEventRecord.payload).where(
                            AgentEventRecord.agent_session_id == ids["done"]
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert all(payload == '{"kind":"new"}' for payload in kinds)

    asyncio.run(_run())


def test_dry_run_measures_without_deleting() -> None:
    _init_db()

    async def _run() -> None:
        await _configure(days=30, cap=0)
        ids = await _seed()

        preview = await prune_agent_events(dry_run=True)
        assert preview.rows == 3
        assert preview.bytes > 0
        # Nothing removed — the preview is what backs the cockpit's estimate.
        assert await _event_count(ids["done"]) == 5

    asyncio.run(_run())


def test_levers_overlap_without_double_counting() -> None:
    """An event caught by both the window and the cap is removed (and counted) once."""
    _init_db()

    async def _run() -> None:
        await _configure(days=30, cap=2)
        ids = await _seed()

        assert (await prune_agent_events()).rows == 3
        assert await _event_count(ids["done"]) == 2
        # Second pass has nothing left to do.
        assert (await prune_agent_events()).rows == 0

    asyncio.run(_run())


def test_large_backlog_stays_under_the_sqlite_parameter_ceiling() -> None:
    """A backlog bigger than SQLite's host-parameter limit is measured and pruned.

    An unchunked ``IN (…)`` over every prunable id failed with "too many SQL
    variables" on every startup once a real install passed 32766 events. The
    limit is compiled into the SQLite build (999 on older ones), so rather than
    seed tens of thousands of rows the test asserts no statement binds more than
    the lowest ceiling while sweeping a backlog above it.
    """
    _init_db()
    backlog = 1_200
    payload = '{"kind":"old"}'
    widest: list[int] = []

    def _record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        if not executemany:
            widest.append(len(parameters or ()))

    async def _run() -> None:
        await _configure(days=30, cap=0)
        old = datetime.now(UTC) - timedelta(days=40)
        async with SessionLocal() as session:
            await session.execute(delete(AgentEventRecord))
            await session.execute(delete(AgentSession))
            done = AgentSession(title="Done", task_prompt="x", status="completed")
            session.add(done)
            await session.flush()
            await session.execute(
                insert(AgentEventRecord),
                [
                    {"agent_session_id": done.id, "payload": payload, "created_at": old}
                    for _ in range(backlog)
                ],
            )
            await session.commit()
            agent_id = done.id

        event.listen(engine.sync_engine, "before_cursor_execute", _record)
        try:
            preview = await prune_agent_events(dry_run=True)
            assert (preview.rows, preview.bytes) == (backlog, backlog * len(payload))
            assert await _event_count(agent_id) == backlog

            assert (await prune_agent_events()).rows == backlog
            assert await _event_count(agent_id) == 0
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _record)
        assert max(widest) < 999

    asyncio.run(_run())
