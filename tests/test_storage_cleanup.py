"""Tests for the storage cockpit endpoints.

The cockpit is the user-facing half of retention: it previews what each sweep
would free, runs one on demand ahead of its daily ticker, and compacts the
database so freed pages actually leave the file. Covers: the preview lists every
target without deleting anything, running a target reports what it removed, an
unknown target 404s, and compaction reports a size delta.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import delete

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentEventRecord, AgentSession
from precursor.backend.services.storage_cleanup import TARGETS


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


def test_compact_reports_a_size_delta() -> None:
    with TestClient(create_app()) as client:
        resp = client.post("/api/stats/compact")
        assert resp.status_code == 200
        body = resp.json()
        assert body["supported"] is True
        assert body["error"] is None
        assert body["size_before"] is not None
        assert body["size_after"] is not None
        # Compaction must never make the file bigger — it did before the WAL was
        # checkpointed on *both* sides of the VACUUM (a VACUUM in WAL mode
        # rewrites the whole database through the WAL).
        assert body["size_after"] <= body["size_before"]
