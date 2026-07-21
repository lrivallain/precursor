"""Tests for age-limited retention of Live meeting transcript segments.

The sweep deletes ``MeetingSegment`` rows belonging to sessions that ended more
than the retention window ago, while leaving the session (and its insights /
notes / summary) intact. Covers: disabled (0) is a no-op; segments of long-ended
sessions are deleted while recent-ended and still-active sessions keep theirs;
and the parent session row survives the cleanup.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting, MeetingSegment, MeetingSession
from precursor.backend.services.live_transcript_retention import prune_expired_live_transcripts


def _init_db() -> None:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass


async def _set_retention(days: int) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "live_transcript_retention_days")
        encoded = json.dumps(days)
        if row is None:
            session.add(AppSetting(key="live_transcript_retention_days", value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def _seed() -> dict[str, int]:
    """Create three sessions (old-ended, recent-ended, active) with segments."""
    old = datetime.now(UTC) - timedelta(days=40)
    recent = datetime.now(UTC) - timedelta(days=1)
    async with SessionLocal() as session:
        # Isolate from other tests sharing the session-wide temp DB.
        await session.execute(delete(MeetingSegment))
        await session.execute(delete(MeetingSession))

        old_ended = MeetingSession(title="Old", slug="old", status="ended", ended_at=old)
        recent_ended = MeetingSession(
            title="Recent", slug="recent", status="ended", ended_at=recent
        )
        # Active/paused: no ended_at, but created long ago — must never be pruned.
        active = MeetingSession(title="Active", slug="active", status="active", created_at=old)
        session.add_all([old_ended, recent_ended, active])
        await session.flush()
        ids = {"old": old_ended.id, "recent": recent_ended.id, "active": active.id}
        session.add_all(
            [
                MeetingSegment(session_id=old_ended.id, text="a"),
                MeetingSegment(session_id=old_ended.id, text="b"),
                MeetingSegment(session_id=recent_ended.id, text="c"),
                MeetingSegment(session_id=active.id, text="d"),
            ]
        )
        await session.commit()
    return ids


async def _segment_count(session_id: int) -> int:
    async with SessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(MeetingSegment)
                    .where(MeetingSegment.session_id == session_id)
                )
            ).scalar_one()
        )


def test_retention_disabled_is_noop() -> None:
    _init_db()

    async def _run() -> None:
        await _set_retention(0)
        ids = await _seed()
        assert await prune_expired_live_transcripts() == 0
        assert await _segment_count(ids["old"]) == 2

    asyncio.run(_run())


def test_prunes_only_long_ended_sessions() -> None:
    _init_db()

    async def _run() -> None:
        await _set_retention(30)
        ids = await _seed()
        # Two segments belong to the old-ended session.
        assert await prune_expired_live_transcripts() == 2
        assert await _segment_count(ids["old"]) == 0
        # Recent-ended and still-active sessions keep their transcript.
        assert await _segment_count(ids["recent"]) == 1
        assert await _segment_count(ids["active"]) == 1
        # The parent session row survives the cleanup.
        async with SessionLocal() as session:
            assert await session.get(MeetingSession, ids["old"]) is not None

    asyncio.run(_run())


def test_idempotent_second_run() -> None:
    _init_db()

    async def _run() -> None:
        await _set_retention(30)
        await _seed()
        assert await prune_expired_live_transcripts() == 2
        assert await prune_expired_live_transcripts() == 0

    asyncio.run(_run())
