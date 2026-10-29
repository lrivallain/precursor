"""Editing an assistant reply in place, keeping the model's original to restore."""

from __future__ import annotations

from typing import Any

import anyio
from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def _seed(kind: str, container_id: int, prompt: str, reply: str) -> tuple[int, int]:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Message, MessageRole

    fk = {f"{kind}_id": container_id}

    async def _go() -> tuple[int, int]:
        async with SessionLocal() as session:
            user = Message(role=MessageRole.USER, content=prompt, **fk)
            session.add(user)
            await session.flush()
            assistant = Message(role=MessageRole.ASSISTANT, content=reply, **fk)
            session.add(assistant)
            await session.commit()
            return user.id, assistant.id

    result: tuple[int, int] = anyio.run(_go)
    return result


def _history(topic_id: int) -> list[tuple[str, str]]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Message
    from precursor.backend.services.turn_engine import hydrate_history

    async def _go() -> list[tuple[str, str]]:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Message)
                    .where(Message.topic_id == topic_id)
                    .options(selectinload(Message.attachments))
                    .order_by(Message.id)
                )
            ).scalars()
            return [(m.role, m.content) for m in hydrate_history(list(rows))]

    result: list[tuple[str, str]] = anyio.run(_go)
    return result


def _topic(client: TestClient) -> dict[str, Any]:
    res = client.post("/api/topics", json={"title": "Edit replies"})
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data


def test_editing_a_topic_reply_keeps_the_original_once() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client)
        _, reply_id = _seed("topic", topic["id"], "Draft an email", "Dear Bob,")
        url = f"/api/topics/{topic['id']}/messages/{reply_id}"

        first = client.patch(url, json={"content": "Dear Robert,"})
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["content"] == "Dear Robert,"
        assert body["original_content"] == "Dear Bob,"
        assert body["edited_at"] is not None

        second = client.patch(url, json={"content": "Hi Robert,"}).json()
        assert second["content"] == "Hi Robert,"
        assert second["original_content"] == "Dear Bob,"

        listed = client.get(f"/api/topics/{topic['id']}/messages").json()
        assert [m["content"] for m in listed] == ["Draft an email", "Hi Robert,"]
        assert listed[1]["original_content"] == "Dear Bob,"


def test_later_turns_see_the_edited_reply() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client)
        _, reply_id = _seed("topic", topic["id"], "Draft an email", "Dear Bob,")
        client.patch(
            f"/api/topics/{topic['id']}/messages/{reply_id}", json={"content": "Dear Robert,"}
        )
        assert _history(topic["id"]) == [("user", "Draft an email"), ("assistant", "Dear Robert,")]


def test_saving_the_original_text_restores_it() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client)
        _, reply_id = _seed("topic", topic["id"], "Draft", "Original")
        url = f"/api/topics/{topic['id']}/messages/{reply_id}"
        client.patch(url, json={"content": "Edited"})

        restored = client.patch(url, json={"content": "Original"}).json()
        assert restored["content"] == "Original"
        assert restored["original_content"] is None
        assert restored["edited_at"] is None


def test_chat_replies_are_editable_too() -> None:
    with TestClient(create_app()) as client:
        chat_id = client.post("/api/chats", json={"title": "Edit chat"}).json()["id"]
        _, reply_id = _seed("chat", chat_id, "Draft", "Original")

        res = client.patch(f"/api/chats/{chat_id}/messages/{reply_id}", json={"content": "New"})
        assert res.status_code == 200, res.text
        assert res.json()["original_content"] == "Original"


def test_only_assistant_replies_of_this_container_can_be_edited() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client)
        other = _topic(client)
        prompt_id, reply_id = _seed("topic", topic["id"], "Draft", "Original")

        prompt = client.patch(
            f"/api/topics/{topic['id']}/messages/{prompt_id}", json={"content": "x"}
        )
        assert prompt.status_code == 422

        elsewhere = client.patch(
            f"/api/topics/{other['id']}/messages/{reply_id}", json={"content": "x"}
        )
        assert elsewhere.status_code == 404

        blank = client.patch(
            f"/api/topics/{topic['id']}/messages/{reply_id}", json={"content": " "}
        )
        assert blank.status_code == 422
