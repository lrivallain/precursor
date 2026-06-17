"""Smoke tests for the FastAPI app — ensures imports + lifespan wire up cleanly."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_create_app_and_health() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert isinstance(body.get("version"), str) and body["version"]


def test_topics_empty_tree() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/topics/tree")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_chats_crud_lifecycle() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Empty to start.
        r = client.get("/api/chats")
        assert r.status_code == 200
        assert r.json() == []

        # Create a chat.
        r = client.post("/api/chats", json={"title": "My first chat"})
        assert r.status_code == 201
        chat = r.json()
        assert chat["title"] == "My first chat"
        assert chat["slug"] == "my-first-chat"
        assert chat["unread_count"] == 0
        chat_id = chat["id"]

        # It shows up in the list.
        r = client.get("/api/chats")
        assert [c["id"] for c in r.json()] == [chat_id]

        # Messages endpoint exists and is empty.
        r = client.get(f"/api/chats/{chat_id}/messages")
        assert r.status_code == 200
        assert r.json() == []

        # Update it.
        r = client.patch(f"/api/chats/{chat_id}", json={"title": "Renamed", "pinned": True})
        assert r.status_code == 200
        assert r.json()["title"] == "Renamed"
        assert r.json()["pinned"] is True

        # Archive / unarchive.
        r = client.post(f"/api/chats/{chat_id}/archive")
        assert r.status_code == 200
        assert r.json()["archived_at"] is not None
        assert client.get("/api/chats").json() == []
        assert [c["id"] for c in client.get("/api/chats/archived").json()] == [chat_id]

        r = client.post(f"/api/chats/{chat_id}/unarchive")
        assert r.status_code == 200
        assert r.json()["archived_at"] is None

        # Mark read.
        assert client.post(f"/api/chats/{chat_id}/read").status_code == 204

        # Delete.
        assert client.delete(f"/api/chats/{chat_id}").status_code == 204
        assert client.get("/api/chats").json() == []
        assert client.get(f"/api/chats/{chat_id}").status_code == 404


def test_log_config_unifies_format() -> None:
    import logging
    import re

    from precursor.backend.logging_config import UTCFormatter, build_log_config

    cfg = build_log_config("debug", color=False)
    # uvicorn + noisy third-party loggers route through the single root handler.
    assert cfg["root"]["handlers"] == ["default"]
    # Root level follows the app log_level (so precursor.* honours debug)...
    assert cfg["root"]["level"] == "DEBUG"
    # ...but noisy deps stay pinned regardless, so app DEBUG never unleashes
    # per-statement library spam.
    assert cfg["loggers"]["aiosqlite"]["level"] == "WARNING"
    assert cfg["loggers"]["sqlalchemy.engine"]["level"] == "WARNING"
    assert cfg["loggers"]["sse_starlette.sse"]["level"] == "INFO"
    assert cfg["loggers"]["openai._base_client"]["level"] == "WARNING"
    assert cfg["loggers"]["mcp.client"]["level"] == "WARNING"
    for name in ("uvicorn", "uvicorn.access", "mcp", "httpx", "watchfiles"):
        assert cfg["loggers"][name]["handlers"] == []
        assert cfg["loggers"][name]["propagate"] is True
    # Keeps import-time module loggers alive so they propagate to root.
    assert cfg["disable_existing_loggers"] is False

    record = logging.LogRecord(
        "precursor.backend.services.scheduler",
        logging.INFO,
        __file__,
        1,
        "Scheduler started",
        None,
        None,
    )
    line = UTCFormatter(color=False).format(record)
    timestamp = line.split(" ", 1)[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp)
    assert "INFO" in line
    assert "precursor.backend.services.scheduler" in line
    assert line.endswith("Scheduler started")
    # Plain mode emits no ANSI escapes; colour mode wraps the level.
    assert "\033[" not in line
    assert "\033[" in UTCFormatter(color=True).format(record)
