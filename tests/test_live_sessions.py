"""Live meeting assistant API tests — session CRUD lifecycle.

Phase 1 covers the session lifecycle only (create / list / get / update /
delete) plus the optional topic link. Transcript ingestion, live analysis, and
summary attachment are exercised in later phases.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_meeting_session_crud_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Create with no topic and no title — server generates a dated title.
        created = client.post("/api/live", json={})
        assert created.status_code == 201
        body = created.json()
        sid = body["id"]
        assert body["status"] == "active"
        assert body["slug"]
        assert body["title"]
        assert body["topic_id"] is None
        assert body["ended_at"] is None

        # It shows up in the list.
        listing = client.get("/api/live")
        assert listing.status_code == 200
        assert any(s["id"] == sid for s in listing.json())

        # Fetch by id.
        got = client.get(f"/api/live/{sid}")
        assert got.status_code == 200
        assert got.json()["id"] == sid

        # Rename + set language.
        patched = client.patch(
            f"/api/live/{sid}", json={"title": "Sprint sync", "language": "fr-FR"}
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Sprint sync"
        assert patched.json()["language"] == "fr-FR"

        # Ending the session stamps ended_at.
        ended = client.patch(f"/api/live/{sid}", json={"status": "ended"})
        assert ended.status_code == 200
        assert ended.json()["status"] == "ended"
        assert ended.json()["ended_at"] is not None

        # Delete.
        deleted = client.delete(f"/api/live/{sid}")
        assert deleted.status_code == 204
        assert client.get(f"/api/live/{sid}").status_code == 404


def test_meeting_session_topic_link_and_validation() -> None:
    app = create_app()
    with TestClient(app) as client:
        topic = client.post("/api/topics", json={"title": "Context topic"})
        assert topic.status_code in (200, 201)
        tid = topic.json()["id"]

        # Attaching an existing topic works.
        created = client.post("/api/live", json={"title": "Kickoff", "topic_id": tid})
        assert created.status_code == 201
        assert created.json()["topic_id"] == tid

        # A non-existent topic is rejected.
        bad = client.post("/api/live", json={"topic_id": 999_999})
        assert bad.status_code == 400

        # Detaching the topic (null) is allowed.
        sid = created.json()["id"]
        detached = client.patch(f"/api/live/{sid}", json={"topic_id": None})
        assert detached.status_code == 200
        assert detached.json()["topic_id"] is None


def test_meeting_session_slugs_are_unique() -> None:
    app = create_app()
    with TestClient(app) as client:
        a = client.post("/api/live", json={"title": "Standup"})
        b = client.post("/api/live", json={"title": "Standup"})
        assert a.status_code == b.status_code == 201
        assert a.json()["slug"] != b.json()["slug"]
