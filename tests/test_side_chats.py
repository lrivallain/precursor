"""Side chats — chats started from a topic or from one of its replies."""

from __future__ import annotations

from typing import Any

import anyio
from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def _run(fn: Any) -> Any:
    return anyio.run(fn)


def _seed_turn(topic_id: int, prompt: str, reply: str) -> tuple[int, int]:
    """Insert a user prompt and its assistant reply; return their ids."""
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Message, MessageRole

    async def _go() -> tuple[int, int]:
        async with SessionLocal() as session:
            user = Message(topic_id=topic_id, role=MessageRole.USER, content=prompt)
            session.add(user)
            await session.flush()
            assistant = Message(topic_id=topic_id, role=MessageRole.ASSISTANT, content=reply)
            session.add(assistant)
            await session.commit()
            return user.id, assistant.id

    result: tuple[int, int] = _run(_go)
    return result


def _set_summary(topic_id: int, content: str) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import TopicSummary

    async def _go() -> None:
        async with SessionLocal() as session:
            session.add(TopicSummary(topic_id=topic_id, content=content))
            await session.commit()

    _run(_go)


def _context(chat_id: int) -> str:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Chat
    from precursor.backend.services.turn_engine import build_chat_system_context

    async def _go() -> str:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            return await build_chat_system_context(session, chat)

    result: str = _run(_go)
    return result


def _topic(client: TestClient, title: str, **extra: Any) -> dict[str, Any]:
    res = client.post("/api/topics", json={"title": title, **extra})
    assert res.status_code == 201, res.text
    data: dict[str, Any] = res.json()
    return data


def test_whole_topic_side_chat_is_grounded_in_the_topic() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Side chat parent", description="Migrate the billing DB")
        _set_summary(topic["id"], "Cutover planned for Friday.")

        res = client.post(f"/api/topics/{topic['id']}/chats", json={})
        assert res.status_code == 201, res.text
        chat = res.json()
        assert chat["parent_topic_id"] == topic["id"]
        assert chat["parent_topic_title"] == "Side chat parent"
        assert chat["parent_message_id"] is None
        assert chat["seed_content"] is None

        ctx = _context(chat["id"])
        assert "Parent topic title: Side chat parent" in ctx
        assert "Parent topic description: Migrate the billing DB" in ctx
        assert "Cutover planned for Friday." in ctx
        assert "<<<REPLY" not in ctx

        # The chat list carries the link too, for its "from <topic>" chip.
        listed = next(c for c in client.get("/api/chats").json() if c["id"] == chat["id"])
        assert listed["parent_topic_title"] == "Side chat parent"


def test_side_chat_from_a_reply_freezes_a_copy_of_it() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Reply parent")
        _, reply_id = _seed_turn(topic["id"], "Compare A and B", "A is faster; B is cheaper.")

        chat = client.post(f"/api/topics/{topic['id']}/chats", json={"message_id": reply_id}).json()
        assert chat["parent_message_id"] == reply_id
        assert chat["seed_content"] == "A is faster; B is cheaper."
        assert "A is faster; B is cheaper." in _context(chat["id"])

        items = client.get(f"/api/topics/{topic['id']}/chats").json()
        assert [i["id"] for i in items] == [chat["id"]]
        assert items[0]["from_reply"] is True
        assert items[0]["message_count"] == 0
        assert items[0]["reminder"] is None


def test_side_chat_rejects_invalid_sources() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Validation parent")
        other = _topic(client, "Validation other")
        prompt_id, reply_id = _seed_turn(topic["id"], "q", "a")

        assert client.post("/api/topics/999999/chats", json={}).status_code == 404
        # The reply must belong to the topic …
        res = client.post(f"/api/topics/{other['id']}/chats", json={"message_id": reply_id})
        assert res.status_code == 404
        # … and be an assistant reply.
        res = client.post(f"/api/topics/{topic['id']}/chats", json={"message_id": prompt_id})
        assert res.status_code == 422
        assert client.get("/api/topics/999999/chats").status_code == 404


def test_rewinding_the_source_keeps_the_copy_but_drops_the_link() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Rewind parent")
        prompt_id, reply_id = _seed_turn(topic["id"], "q", "the reply")
        chat = client.post(f"/api/topics/{topic['id']}/chats", json={"message_id": reply_id}).json()

        res = client.post(
            f"/api/topics/{topic['id']}/messages/rewind", json={"from_message_id": prompt_id}
        )
        assert res.status_code == 200, res.text

        after = client.get(f"/api/chats/{chat['id']}").json()
        assert after["parent_message_id"] is None
        assert after["seed_content"] == "the reply"
        assert after["parent_topic_id"] == topic["id"]


def test_deleting_the_source_message_drops_the_link() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Delete-message parent")
        _, reply_id = _seed_turn(topic["id"], "q", "deleted reply")
        chat = client.post(f"/api/topics/{topic['id']}/chats", json={"message_id": reply_id}).json()

        res = client.delete(f"/api/topics/{topic['id']}/messages/{reply_id}")
        assert res.status_code == 204
        assert client.get(f"/api/chats/{chat['id']}").json()["parent_message_id"] is None


def test_deleting_the_topic_detaches_its_side_chats() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Doomed parent")
        _, reply_id = _seed_turn(topic["id"], "q", "kept reply")
        chat = client.post(f"/api/topics/{topic['id']}/chats", json={"message_id": reply_id}).json()

        assert client.delete(f"/api/topics/{topic['id']}").status_code == 204

        after = client.get(f"/api/chats/{chat['id']}").json()
        assert after["parent_topic_id"] is None
        assert after["parent_topic_title"] is None
        assert after["parent_message_id"] is None
        # The copied reply still grounds the chat.
        assert after["seed_content"] == "kept reply"
        ctx = _context(chat["id"])
        assert "kept reply" in ctx
        assert "Parent topic title" not in ctx


def test_archived_side_chats_leave_the_topic_panel() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Archive parent")
        chat = client.post(f"/api/topics/{topic['id']}/chats", json={}).json()
        assert len(client.get(f"/api/topics/{topic['id']}/chats").json()) == 1

        client.post(f"/api/chats/{chat['id']}/archive")
        assert client.get(f"/api/topics/{topic['id']}/chats").json() == []

        restored = client.post(f"/api/chats/{chat['id']}/unarchive").json()
        assert restored["parent_topic_id"] == topic["id"]
        assert len(client.get(f"/api/topics/{topic['id']}/chats").json()) == 1


def test_side_chat_inherits_the_topic_role() -> None:
    with TestClient(create_app()) as client:
        role = client.post(
            "/api/roles", json={"name": "Side chat reviewer", "system_prompt": "Be terse."}
        ).json()
        topic = _topic(client, "Role parent")
        client.patch(f"/api/topics/{topic['id']}", json={"role_id": role["id"]})

        chat = client.post(f"/api/topics/{topic['id']}/chats", json={}).json()
        assert chat["role_id"] == role["id"]
        client.delete(f"/api/chats/{chat['id']}")
        client.delete(f"/api/roles/{role['id']}")


def test_mcp_chat_dict_exposes_the_parent_link() -> None:
    from precursor.backend.models import Chat
    from precursor.backend.services.mcp.precursor_server import _chat_dict

    with TestClient(create_app()) as client:
        topic = _topic(client, "MCP parent")
        chat_id = client.post(f"/api/topics/{topic['id']}/chats", json={}).json()["id"]

    from precursor.backend.db import SessionLocal

    async def _go() -> dict[str, Any]:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            return _chat_dict(chat)

    data = _run(_go)
    assert data["parent_topic_id"] == topic["id"]
    assert data["parent_message_id"] is None


def test_promoting_a_side_chat_makes_a_sub_topic_of_its_parent() -> None:
    with TestClient(create_app()) as client:
        home = client.post("/api/collections", json={"name": "Side chat home"}).json()
        other = client.post("/api/collections", json={"name": "Side chat elsewhere"}).json()
        topic = _topic(client, "Promote parent", collection_id=home["id"])
        chat = client.post(f"/api/topics/{topic['id']}/chats", json={}).json()

        # The collection the caller is looking at loses to the parent's: a
        # subtree never spans collections.
        promoted = client.post(f"/api/chats/{chat['id']}/promote?collection_id={other['id']}")
        assert promoted.status_code == 200, promoted.text
        data = promoted.json()
        assert data["parent_id"] == topic["id"]
        assert data["collection_id"] == home["id"]
        assert client.get(f"/api/chats/{chat['id']}").status_code == 404
        assert client.get(f"/api/topics/{topic['id']}/chats").json() == []


def test_promoting_a_detached_side_chat_makes_a_root_topic() -> None:
    with TestClient(create_app()) as client:
        topic = _topic(client, "Promote doomed parent")
        chat = client.post(f"/api/topics/{topic['id']}/chats", json={}).json()
        client.delete(f"/api/topics/{topic['id']}")

        data = client.post(f"/api/chats/{chat['id']}/promote").json()
        assert data["parent_id"] is None


def test_promoting_keeps_the_quoted_reply_in_the_topic() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import Topic
    from precursor.backend.services.turn_engine import build_system_context

    with TestClient(create_app()) as client:
        topic = _topic(client, "Promote seed parent")
        _, reply_id = _seed_turn(topic["id"], "q", "Use blue-green deploys.")
        chat = client.post(f"/api/topics/{topic['id']}/chats", json={"message_id": reply_id}).json()

        promoted = client.post(f"/api/chats/{chat['id']}/promote").json()
        assert promoted["seed_content"] == "Use blue-green deploys."
        assert client.get(f"/api/topics/{promoted['id']}").json()["seed_content"] == (
            "Use blue-green deploys."
        )

        async def _go() -> str:
            async with SessionLocal() as session:
                row = await session.get(Topic, promoted["id"])
                assert row is not None
                return await build_system_context(session, row)

        ctx: str = _run(_go)
        assert "started this topic from the following assistant reply" in ctx
        assert "Use blue-green deploys." in ctx

        # An ordinary topic carries no quote.
        assert topic["seed_content"] is None
