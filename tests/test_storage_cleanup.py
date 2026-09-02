"""Tests for the storage cockpit endpoints.

The cockpit is the user-facing half of retention: it previews what each sweep
would free, runs one on demand ahead of its daily ticker, and compacts the
database so freed pages actually leave the file. Covers: the preview lists every
target without deleting anything, running a target reports what it removed, an
unknown target 404s, the compact endpoint reports coherent sizes, and
compaction actually shrinks a database it is given to shrink.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentEventRecord, AgentSession
from precursor.backend.services.storage_cleanup import TARGETS, compact_database


def _seed_oversized() -> None:
    async def _run() -> None:
        async with SessionLocal() as session:
            await session.execute(delete(AgentEventRecord))
            await session.execute(delete(AgentSession))
            agent = AgentSession(title="A", task_prompt="x", status="completed")
            session.add(agent)
            await session.flush()
            session.add(
                AgentEventRecord(
                    agent_session_id=agent.id,
                    payload=json.dumps({"kind": "HookStartData", "data": {"input": "x" * 50_000}}),
                )
            )
            await session.commit()

    asyncio.run(_run())


def test_preview_lists_every_target_without_deleting() -> None:
    with TestClient(create_app()) as client:
        _seed_oversized()

        resp = client.get("/api/stats/cleanup")
        assert resp.status_code == 200
        body = resp.json()

        keys = {t["key"] for t in body["targets"]}
        assert keys == {t.key for t in TARGETS}
        oversized = next(t for t in body["targets"] if t["key"] == "oversized_events")
        assert oversized["rows"] == 1
        assert oversized["bytes"] > 0
        assert body["total_bytes"] >= oversized["bytes"]

        # A preview must not have removed anything — re-reading reports the same.
        assert client.get("/api/stats/cleanup").json()["total_bytes"] == body["total_bytes"]


def test_running_a_target_reports_what_it_removed() -> None:
    with TestClient(create_app()) as client:
        _seed_oversized()

        resp = client.post("/api/stats/cleanup/oversized_events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "oversized_events"
        assert body["rows"] == 1
        assert body["bytes"] > 0

        # The target is now clean, so the preview drops back to zero.
        after = client.get("/api/stats/cleanup").json()
        oversized = next(t for t in after["targets"] if t["key"] == "oversized_events")
        assert oversized["rows"] == 0


def test_unknown_target_is_rejected() -> None:
    with TestClient(create_app()) as client:
        resp = client.post("/api/stats/cleanup/not-a-target")
        assert resp.status_code == 404


def _file_size(path: Path) -> int:
    """Size of ``path``, or 0 when SQLite has already removed the sidecar."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


@asynccontextmanager
async def _churned_engine(db_path: Path) -> AsyncIterator[AsyncEngine]:
    """A throwaway SQLite database holding a known amount of reclaimable space.

    Mirrors the PRAGMAs ``db.py`` puts on the real engine — ``journal_mode=WAL``
    above all, since the regression compaction guards against is WAL-specific —
    then writes ~8 MB and deletes most of it. ``auto_vacuum`` is off, so the file
    stays that size, mostly free pages, until a VACUUM rebuilds it.

    Deleting *most* rather than *all* of it is the load-bearing part. VACUUM in
    WAL mode rewrites the surviving database through the WAL, so the rows kept
    here are what a missing post-VACUUM checkpoint would strand there: with
    nothing left alive the rebuilt database is a single page, the WAL stays tiny
    and the regression slips through a size comparison unnoticed.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    rows, kept = 2_000, 800
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE churn (id INTEGER PRIMARY KEY, blob TEXT)"))
            await conn.execute(
                text("INSERT INTO churn (blob) VALUES (:blob)"),
                [{"blob": "x" * 2048} for _ in range(rows)],
            )
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM churn WHERE id > :kept"), {"kept": kept})
        # Fold the write-ahead log into the file before handing it over. Without
        # this the fixture starts with the whole 8 MB of churn still pending in
        # the WAL, which inflates ``size_before`` enough to hide the very
        # regression under test; a database that has been running for a while is
        # checkpointed, not carrying its entire history in a sidecar.
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        yield engine
    finally:
        await engine.dispose()


async def test_compaction_shrinks_a_churned_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compaction returns freed pages to the filesystem.

    Asserted against a database this test owns rather than the suite-wide one.
    Every test shares a single SQLite file, so there the inequality is a property
    of whatever the preceding thousand tests happened to leave behind — and
    VACUUM is not obliged to shrink a file whose free-page layout is already
    compact. Controlling the churn makes the assertion *strict* (compaction has
    to actually reclaim) instead of intermittent.
    """
    from precursor.backend import db as db_module

    db_path = tmp_path / "compact.db"
    async with _churned_engine(db_path) as engine:
        # ``compact_database`` imports the engine inside its body, so patching
        # the module attribute is what points it at our file.
        monkeypatch.setattr(db_module, "engine", engine)
        result = await compact_database()
        wal_bytes = _file_size(db_path.with_name(db_path.name + "-wal"))

    assert result.supported is True
    assert result.error is None
    assert result.size_before is not None
    assert result.size_after is not None
    # Compaction must never make the file bigger — it did before the WAL was
    # checkpointed on *both* sides of the VACUUM (a VACUUM in WAL mode rewrites
    # the whole database through the WAL). Dropping that second checkpoint takes
    # this fixture from ~8.3 MB to ~11.6 MB.
    assert result.size_after < result.size_before
    assert result.reclaimed > 0
    # The same regression, named directly, so a future fixture that trims the
    # churn can't quietly defang the check above.
    assert wal_bytes == 0


def test_compact_endpoint_reports_sizes() -> None:
    """The endpoint reports a coherent pair of sizes and no error.

    Sizes only: the suite shares one database, so *how much* a VACUUM reclaims
    here is not this test's to control. The shrink assertion lives in
    ``test_compaction_shrinks_a_churned_database``, which owns its file.
    """
    with TestClient(create_app()) as client:
        resp = client.post("/api/stats/compact")
        assert resp.status_code == 200
        body = resp.json()
        assert body["supported"] is True
        assert body["error"] is None
        assert body["size_before"] is not None
        assert body["size_after"] is not None
        assert body["reclaimed_bytes"] == max(body["size_before"] - body["size_after"], 0)
