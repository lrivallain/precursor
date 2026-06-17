"""Smoke tests for the FastAPI app — ensures imports + lifespan wire up cleanly."""

from __future__ import annotations

import re
from pathlib import Path

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


def test_dev_rebuild_messages_table_adds_chat_support(tmp_path: Path) -> None:
    """Old-schema messages (no chat_id, NOT NULL topic_id) rebuild cleanly.

    Guards the dev backfill in ``_ensure_dev_columns``: it must add chat_id,
    relax topic_id to nullable, preserve rows, recreate indexes, and leave no
    ``_messages_old`` behind — even though the renamed table carries the old
    ``ix_messages_topic_id`` name.
    """
    from sqlalchemy import create_engine, text

    from precursor.backend.db import _ensure_dev_columns

    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE topics (id INTEGER PRIMARY KEY, title TEXT, slug TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE messages ("
                "  id INTEGER PRIMARY KEY,"
                "  topic_id INTEGER NOT NULL,"
                "  role VARCHAR(9) NOT NULL,"
                "  content TEXT NOT NULL,"
                "  tool_calls TEXT,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,"
                "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,"
                "  FOREIGN KEY(topic_id) REFERENCES topics (id) ON DELETE CASCADE"
                ")"
            )
        )
        conn.execute(text("CREATE INDEX ix_messages_topic_id ON messages(topic_id)"))
        conn.execute(text("INSERT INTO topics (id, title, slug) VALUES (1, 'T', 't')"))
        conn.execute(
            text("INSERT INTO messages (id, topic_id, role, content) VALUES (1, 1, 'user', 'hi')")
        )

    with engine.begin() as conn:
        _ensure_dev_columns(conn)

    with engine.connect() as conn:
        info = conn.execute(text("PRAGMA table_info(messages)")).fetchall()
        by_name = {row[1]: row for row in info}
        assert "chat_id" in by_name
        # topic_id must now be nullable (notnull flag == 0).
        assert by_name["topic_id"][3] == 0
        # The existing row survived the rebuild.
        assert conn.execute(text("SELECT content FROM messages WHERE id = 1")).scalar() == "hi"
        # Both indexes exist on the rebuilt table; the temp table is gone.
        idx = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='messages'")
            ).fetchall()
        }
        assert {"ix_messages_topic_id", "ix_messages_chat_id"} <= idx
        assert (
            conn.execute(text("SELECT name FROM sqlite_master WHERE name='_messages_old'")).first()
            is None
        )
    engine.dispose()


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
        # Default chat slug is a random UUID hex (not derived from the title),
        # so repeated "New chat" creates don't pile up new-chat-2, new-chat-3…
        assert re.fullmatch(r"[0-9a-f]{32}", chat["slug"])
        assert chat["unread_count"] == 0
        chat_id = chat["id"]
        slug = chat["slug"]

        # An explicit slug is still honoured (and slugified).
        r2 = client.post("/api/chats", json={"title": "Other", "slug": "My Custom Slug"})
        assert r2.status_code == 201
        assert r2.json()["slug"] == "my-custom-slug"
        client.delete(f"/api/chats/{r2.json()['id']}")

        # Resolvable by slug for /chats/<slug> deep links.
        r = client.get(f"/api/chats/by-slug/{slug}")
        assert r.status_code == 200
        assert r.json()["id"] == chat_id
        assert client.get("/api/chats/by-slug/nope").status_code == 404

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


def test_chat_message_serialization_handles_null_topic_id() -> None:
    """Chat messages have topic_id=None; the read model must allow it.

    Regression: MessageRead.topic_id was a required int, so GET on a chat's
    messages 500'd with a ResponseValidationError.
    """
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Notes chat"}).json()["id"]
        # /notes append persists a user message without invoking the LLM.
        r = client.post(f"/api/chats/{cid}/messages/notes/append", json={"text": "hello"})
        assert r.status_code == 200
        msg = r.json()["message"]
        assert msg["topic_id"] is None
        assert msg["chat_id"] == cid

        r = client.get(f"/api/chats/{cid}/messages")
        assert r.status_code == 200
        msgs = r.json()
        assert len(msgs) == 1
        assert msgs[0]["topic_id"] is None
        assert msgs[0]["chat_id"] == cid


def test_chat_promote_to_topic_moves_messages() -> None:
    """Promoting a chat creates a topic, moves the transcript, drops the chat."""
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Promote me", "description": "ctx"}).json()[
            "id"
        ]
        client.post(f"/api/chats/{cid}/messages/notes/append", json={"text": "carry over"})

        r = client.post(f"/api/chats/{cid}/promote")
        assert r.status_code == 200
        topic = r.json()
        assert topic["title"] == "Promote me"
        assert topic["description"] == "ctx"
        tid = topic["id"]

        # The chat is gone, the message moved onto the new topic.
        assert client.get(f"/api/chats/{cid}").status_code == 404
        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assert any("carry over" in m["content"] for m in msgs)
        assert all(m["topic_id"] == tid and m["chat_id"] is None for m in msgs)


def test_log_config_unifies_format() -> None:
    import logging

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
