"""Reminder API tests — the shared topic/chat flow (issue #26).

A single ``Reminder`` model + service backs both containers, so these tests
drive the HTTP API for a topic *and* a chat through the same lifecycle:
set → fire → appears in the fired list → acknowledge.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.services import reminders as svc


def _fire_due() -> list[object]:
    async def _run() -> list[object]:
        async with SessionLocal() as session:
            return list(await svc.fire_due(session))

    return asyncio.run(_run())


@pytest.mark.parametrize("container", ["topic", "chat"])
def test_reminder_lifecycle_shared_across_containers(container: str) -> None:
    app = create_app()
    with TestClient(app) as client:
        if container == "topic":
            created = client.post("/api/topics", json={"title": "Remind me topic"})
        else:
            created = client.post("/api/chats", json={"title": "Remind me chat"})
        assert created.status_code in (200, 201)
        cid = created.json()["id"]

        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        r = client.put(
            f"/api/reminders/{container}/{cid}",
            json={"remind_at": past, "note": "ping me"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "scheduled"

        # Setting again replaces (still one reminder per container).
        r = client.put(
            f"/api/reminders/{container}/{cid}",
            json={"remind_at": past, "note": "ping me again"},
        )
        assert r.status_code == 200

        # A reminder dated in the past fires almost immediately (the running
        # ticker is nudged by the PUT). Settle any pending fire deterministically.
        _fire_due()

        listing = client.get("/api/reminders").json()
        assert len(listing) == 1
        item = listing[0]
        assert item["container"] == container
        assert item["status"] == "fired"
        assert item["title"].startswith("Remind me")

        # A system message was posted to the discussion.
        if container == "topic":
            msgs = client.get(f"/api/topics/{cid}/messages").json()
        else:
            msgs = client.get(f"/api/chats/{cid}/messages").json()
        assert any("Reminder" in m["content"] for m in msgs)

        # Firing marks the conversation unread, even though it was never opened.
        if container == "topic":
            tree = client.get("/api/topics/tree").json()
            node = next(n for n in tree if n["id"] == cid)
            assert node["unread_count"] >= 1
        else:
            chats = client.get("/api/chats").json()
            chat = next(c for c in chats if c["id"] == cid)
            assert chat["unread_count"] >= 1

        # Acknowledge ("/done") — removes it.
        assert client.delete(f"/api/reminders/{container}/{cid}").status_code == 204
        assert client.get("/api/reminders").json() == []
        # Acknowledging again is a 404 (nothing left).
        assert client.delete(f"/api/reminders/{container}/{cid}").status_code == 404


def test_reminder_set_requires_existing_container() -> None:
    app = create_app()
    with TestClient(app) as client:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        r = client.put("/api/reminders/topic/999999", json={"remind_at": future})
        assert r.status_code == 404


def test_reminder_unknown_container_is_404() -> None:
    app = create_app()
    with TestClient(app) as client:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        r = client.put("/api/reminders/bogus/1", json={"remind_at": future})
        assert r.status_code == 404
