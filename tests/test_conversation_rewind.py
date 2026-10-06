"""Rewinding a conversation and the turn index behind the transcript timeline."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import MESSAGE_KIND_COMPACTION, Attachment, Message, MessageRole
from precursor.backend.services import events as events_mod


def _container(client: TestClient, kind: str, title: str) -> tuple[int, str]:
    plural = "topics" if kind == "topic" else "chats"
    cid = client.post(f"/api/{plural}", json={"title": title}).json()["id"]
    return cid, f"/api/{plural}/{cid}/messages"


def _seed(kind: str, container_id: int) -> dict[str, int]:
    """Three turns: a plain one, a tool round with a compaction, one with an attachment."""
    fk = {"topic_id": container_id} if kind == "topic" else {"chat_id": container_id}
    ids: dict[str, int] = {}

    async def _go() -> None:
        async with SessionLocal() as s:
            rows = [
                ("p1", MessageRole.USER, "First question", None, None),
                ("a1", MessageRole.ASSISTANT, "First answer", None, None),
                ("p2", MessageRole.USER, "Use a tool", None, None),
                ("c2", MessageRole.ASSISTANT, "", None, '[{"id": "t1"}]'),
                ("t2", MessageRole.TOOL, "tool output " * 100, None, None),
                ("a2", MessageRole.ASSISTANT, "Second answer", None, None),
                ("k2", MessageRole.SYSTEM, "Summary", MESSAGE_KIND_COMPACTION, None),
                ("p3", MessageRole.USER, "Read this file", None, None),
                ("a3", MessageRole.ASSISTANT, "Third answer", None, None),
            ]
            for key, role, content, kind_, calls in rows:
                msg = Message(role=role, content=content, kind=kind_, tool_calls=calls, **fk)
                s.add(msg)
                await s.flush()
                ids[key] = msg.id
            att = Attachment(message_id=ids["p3"], mime="text/plain", size=3, sha256="0" * 64, **fk)
            s.add(att)
            await s.flush()
            ids["att"] = att.id
            await s.commit()

    asyncio.run(_go())
    return ids


def _attachment_exists(att_id: int) -> bool:
    async def _go() -> bool:
        async with SessionLocal() as s:
            return (
                await s.execute(select(Attachment.id).where(Attachment.id == att_id))
            ).first() is not None

    return asyncio.run(_go())


@pytest.mark.parametrize("kind", ["topic", "chat"])
def test_turn_index_groups_rows_by_prompt(kind: str) -> None:
    with TestClient(create_app()) as client:
        cid, base = _container(client, kind, "Timeline")
        assert client.get(f"{base}/turns").json() == []
        ids = _seed(kind, cid)

        turns = client.get(f"{base}/turns").json()

        assert [t["message_id"] for t in turns] == [ids["p1"], ids["p2"], ids["p3"]]
        assert [t["last_message_id"] for t in turns] == [ids["a1"], ids["k2"], ids["a3"]]
        assert [t["prompt"] for t in turns] == ["First question", "Use a tool", "Read this file"]
        # The tool-call row has no text: the excerpt is the first real answer.
        assert [t["reply"] for t in turns] == ["First answer", "Second answer", "Third answer"]
        assert [t["has_compaction"] for t in turns] == [False, True, False]


def test_turn_index_truncates_long_prompts() -> None:
    with TestClient(create_app()) as client:
        cid, base = _container(client, "chat", "Long")

        async def _go() -> None:
            async with SessionLocal() as s:
                s.add(Message(role=MessageRole.USER, content="x" * 5000, chat_id=cid))
                await s.commit()

        asyncio.run(_go())
        [turn] = client.get(f"{base}/turns").json()
        assert len(turn["prompt"]) == 200
        assert turn["reply"] == ""


@pytest.mark.parametrize("kind", ["topic", "chat"])
def test_rewind_drops_the_turn_and_everything_after(kind: str) -> None:
    with TestClient(create_app()) as client:
        cid, base = _container(client, kind, "Rewind")
        other_id, other_base = _container(client, kind, "Untouched")
        ids = _seed(kind, cid)
        other = _seed(kind, other_id)

        res = client.post(f"{base}/rewind", json={"from_message_id": ids["p2"]})

        assert res.status_code == 200, res.text
        assert res.json() == {"deleted": 7}
        assert [m["id"] for m in client.get(base).json()] == [ids["p1"], ids["a1"]]
        assert not _attachment_exists(ids["att"])
        # Another conversation of the same kind keeps every row.
        assert len(client.get(other_base).json()) == 9
        assert _attachment_exists(other["att"])


def test_rewind_rejects_foreign_and_non_prompt_rows() -> None:
    with TestClient(create_app()) as client:
        cid, base = _container(client, "chat", "Guarded")
        other_id, _ = _container(client, "chat", "Elsewhere")
        ids = _seed("chat", cid)
        foreign = _seed("chat", other_id)

        assert (
            client.post(f"{base}/rewind", json={"from_message_id": foreign["p1"]}).status_code
            == 404
        )
        assert client.post(f"{base}/rewind", json={"from_message_id": ids["a1"]}).status_code == 422
        assert client.post(f"{base}/rewind", json={"from_message_id": ids["k2"]}).status_code == 422
        assert len(client.get(base).json()) == 9


def test_rewind_is_refused_while_a_reply_is_generating() -> None:
    with TestClient(create_app()) as client:
        cid, base = _container(client, "topic", "Busy")
        ids = _seed("topic", cid)
        events_mod._stream_delta("topic", cid, 1)
        try:
            res = client.post(f"{base}/rewind", json={"from_message_id": ids["p3"]})
            assert res.status_code == 409
            assert "still being generated" in res.json()["detail"]
        finally:
            events_mod._stream_delta("topic", cid, -1)
        assert len(client.get(base).json()) == 9


def test_rewind_keeps_rows_created_after_it_was_confirmed() -> None:
    with TestClient(create_app()) as client:
        cid, base = _container(client, "chat", "Bounded")
        ids = _seed("chat", cid)

        async def _late_note() -> int:
            async with SessionLocal() as s:
                note = Message(role=MessageRole.SYSTEM, content="posted later", chat_id=cid)
                s.add(note)
                await s.commit()
                return note.id

        late = asyncio.run(_late_note())
        res = client.post(
            f"{base}/rewind",
            json={"from_message_id": ids["p2"], "through_message_id": ids["a3"]},
        )

        assert res.json() == {"deleted": 7}
        assert [m["id"] for m in client.get(base).json()] == [ids["p1"], ids["a1"], late]
        bad = client.post(
            f"{base}/rewind",
            json={"from_message_id": ids["p1"], "through_message_id": ids["p1"] - 1},
        )
        assert bad.status_code == 422
