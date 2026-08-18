"""Retrying a failed turn — the `retry_message_id` path of the stream endpoints.

When a provider rejects a turn the backend persists an ``Error: …`` system row
so the failure survives a reload. Replaying that prompt must reuse the original
user message (attachments included) and drop the failed tail, so the transcript
never grows a second copy of the prompt.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.routers import chat as chat_router
from precursor.backend.routers import chat_messages as chat_messages_router
from precursor.backend.services import turn_engine as turn_engine_mod
from precursor.backend.services.llm.base import (
    LLMError,
    TextDeltaEvent,
    TurnDoneEvent,
    UsageEvent,
)


class _RejectingProvider:
    name = "rejecting"

    async def stream_chat(self, *, model, messages, reasoning_effort=None):
        yield ""

    async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
        _ = model, messages, tools, reasoning_effort
        raise LLMError("The requested model is not supported.")
        yield  # pragma: no cover - unreachable, keeps this an async generator

    async def list_models(self):
        return []


class _EchoProvider:
    name = "echo"

    async def stream_chat(self, *, model, messages, reasoning_effort=None):
        yield ""

    async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):
        _ = model, tools, reasoning_effort
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        yield TextDeltaEvent(content=f"answered: {last_user.content if last_user else ''}")
        yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        yield TurnDoneEvent(finish_reason="stop")

    async def list_models(self):
        return []


def _patch_provider(monkeypatch, router, provider) -> None:
    async def _get(_session):
        return provider

    async def _record_usage(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(router, "get_llm_provider", _get)
    monkeypatch.setattr(turn_engine_mod, "record_usage", _record_usage)


def test_topic_retry_replaces_failed_turn(monkeypatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Retry"}).json()["id"]

        _patch_provider(monkeypatch, chat_router, _RejectingProvider())
        failed = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "draw a diagram"},
            headers={"Accept": "text/event-stream"},
        )
        assert failed.status_code == 200

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assert [m["role"] for m in msgs] == ["user", "system"]
        assert msgs[1]["content"].startswith("Error: ")
        user_id = msgs[0]["id"]

        _patch_provider(monkeypatch, chat_router, _EchoProvider())
        retried = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "draw a diagram", "retry_message_id": user_id},
            headers={"Accept": "text/event-stream"},
        )
        assert retried.status_code == 200

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        # The prompt is reused (same id, not duplicated) and the error is gone.
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["id"] == user_id
        assert msgs[0]["content"] == "draw a diagram"
        assert msgs[1]["content"] == "answered: draw a diagram"


def test_topic_retry_keeps_bound_attachments(monkeypatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Retry with file"}).json()["id"]
        aid = client.post(
            f"/api/topics/{tid}/attachments",
            files={"file": ("notes.txt", b"context from the attachment", "text/plain")},
        ).json()["id"]

        _patch_provider(monkeypatch, chat_router, _RejectingProvider())
        client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "summarize", "attachment_ids": [aid]},
            headers={"Accept": "text/event-stream"},
        )
        user_id = client.get(f"/api/topics/{tid}/messages").json()[0]["id"]

        _patch_provider(monkeypatch, chat_router, _EchoProvider())
        client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "summarize", "retry_message_id": user_id},
            headers={"Accept": "text/event-stream"},
        )

        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assert [a["id"] for a in msgs[0]["attachments"]] == [aid]
        # The attachment text is still fed to the model on the replayed turn.
        assert "context from the attachment" in msgs[1]["content"]


def test_retry_rejects_non_user_message(monkeypatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        tid = client.post("/api/topics", json={"title": "Bad retry"}).json()["id"]

        _patch_provider(monkeypatch, chat_router, _EchoProvider())
        client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "hello"},
            headers={"Accept": "text/event-stream"},
        )
        msgs = client.get(f"/api/topics/{tid}/messages").json()
        assistant_id = next(m["id"] for m in msgs if m["role"] == "assistant")

        bad = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "hello", "retry_message_id": assistant_id},
            headers={"Accept": "text/event-stream"},
        )
        assert bad.status_code == 400

        missing = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "hello", "retry_message_id": 999_999},
            headers={"Accept": "text/event-stream"},
        )
        assert missing.status_code == 404


def test_chat_retry_replaces_failed_turn(monkeypatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        cid = client.post("/api/chats", json={"title": "Retry chat"}).json()["id"]

        _patch_provider(monkeypatch, chat_messages_router, _RejectingProvider())
        client.post(
            f"/api/chats/{cid}/messages/stream",
            json={"content": "ping"},
            headers={"Accept": "text/event-stream"},
        )
        msgs = client.get(f"/api/chats/{cid}/messages").json()
        assert [m["role"] for m in msgs] == ["user", "system"]
        user_id = msgs[0]["id"]

        _patch_provider(monkeypatch, chat_messages_router, _EchoProvider())
        client.post(
            f"/api/chats/{cid}/messages/stream",
            json={"content": "ping", "retry_message_id": user_id},
            headers={"Accept": "text/event-stream"},
        )
        msgs = client.get(f"/api/chats/{cid}/messages").json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["id"] == user_id
