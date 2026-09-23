"""Unit coverage for the shared turn preparation in ``services/conversation_turn``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import Attachment, Chat, Message, MessageRole, Topic
from precursor.backend.schemas import ChatRequest
from precursor.backend.services import conversation_turn
from precursor.backend.services.llm.mock import MockProvider


async def _topic_and_chat() -> tuple[int, int]:
    await init_db()
    async with SessionLocal() as session:
        suffix = uuid4().hex[:8]
        topic = Topic(title="Prep", slug=f"prep-{suffix}")
        chat = Chat(title="Prep", slug=f"prep-chat-{suffix}")
        session.add_all([topic, chat])
        await session.commit()
        return topic.id, chat.id


def _attachment(**fk: int) -> Attachment:
    return Attachment(mime="text/plain", size=1, original_filename="a.txt", sha256="0" * 64, **fk)


async def test_persist_user_turn_adopts_only_this_containers_unbound_attachments() -> None:
    topic_id, chat_id = await _topic_and_chat()
    async with SessionLocal() as session:
        mine = _attachment(chat_id=chat_id)
        other_container = _attachment(topic_id=topic_id)
        session.add_all([mine, other_container])
        await session.commit()
        ids = [mine.id, other_container.id]

    async with SessionLocal() as session:
        user_msg, bound = await conversation_turn.persist_user_turn(
            session, "chat", chat_id, ChatRequest(content="look", attachment_ids=ids)
        )
        echo = conversation_turn.user_echo("chat", user_msg, bound)

    assert user_msg.role == MessageRole.USER
    assert user_msg.chat_id == chat_id
    assert [a.id for a in bound] == [ids[0]]
    assert bound[0].message_id == user_msg.id
    assert echo["id"] == user_msg.id
    assert echo["content"] == "look"
    # The echo names the container FK for its kind, as each router always did.
    assert echo["attachments"][0]["chat_id"] == chat_id
    assert "topic_id" not in echo["attachments"][0]

    async with SessionLocal() as session:
        stale = await session.get(Attachment, ids[1])
        assert stale is not None
        assert stale.message_id is None


async def test_persist_user_turn_retry_reuses_the_prompt_and_drops_the_tail() -> None:
    topic_id, _ = await _topic_and_chat()
    async with SessionLocal() as session:
        prompt = Message(topic_id=topic_id, role=MessageRole.USER, content="try me")
        session.add(prompt)
        await session.commit()
        session.add(Message(topic_id=topic_id, role=MessageRole.SYSTEM, content="Error: boom"))
        await session.commit()
        prompt_id = prompt.id

    async with SessionLocal() as session:
        user_msg, bound = await conversation_turn.persist_user_turn(
            session,
            "topic",
            topic_id,
            ChatRequest(content="ignored on retry", retry_message_id=prompt_id),
        )
        history = await conversation_turn.snapshot_history(session, "topic", topic_id)

    assert user_msg.id == prompt_id
    assert bound == []
    assert [(m.role, m.content) for m in history] == [("user", "try me")]


async def test_snapshot_history_swaps_only_the_latest_user_turn_for_the_override() -> None:
    topic_id, _ = await _topic_and_chat()
    async with SessionLocal() as session:
        for role, content in [
            (MessageRole.USER, "first"),
            (MessageRole.ASSISTANT, "answer"),
            (MessageRole.USER, "/to-en bravo"),
        ]:
            session.add(Message(topic_id=topic_id, role=role, content=content))
            await session.commit()

    async with SessionLocal() as session:
        plain = await conversation_turn.snapshot_history(session, "topic", topic_id)
        expanded = await conversation_turn.snapshot_history(
            session, "topic", topic_id, prompt_override="Translate: bravo"
        )

    assert [m.content for m in plain] == ["first", "answer", "/to-en bravo"]
    assert [m.content for m in expanded] == ["first", "answer", "Translate: bravo"]


async def test_resolve_turn_settings_honours_override_and_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _servers(_session: object) -> list[str]:
        return ["precursor", "fetch"]

    async def _model(_session: object) -> str:
        raise AssertionError("the saved model must not be looked up when overridden")

    async def _provider(_session: object) -> MockProvider:
        return MockProvider()

    monkeypatch.setattr(conversation_turn, "load_enabled_mcp_servers", _servers)
    monkeypatch.setattr(conversation_turn, "resolve_llm_model", _model)
    monkeypatch.setattr(conversation_turn, "get_llm_provider", _provider)

    await init_db()
    async with SessionLocal() as session:
        settings = await conversation_turn.resolve_turn_settings(
            session, model_override="picked", exclude_servers={"precursor"}
        )

    assert settings.model == "picked"
    assert settings.enabled_servers == ["fetch"]
    assert isinstance(settings.provider, MockProvider)
    assert settings.max_tool_rounds >= 1
