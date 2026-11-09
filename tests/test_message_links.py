"""Durable identities used by discussion message permalinks."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import (
    MESSAGE_KIND_COMPACTION,
    AgentEventRecord,
    AgentRun,
    AgentSession,
    Chat,
    Message,
    MessageRole,
)
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services import conversation_turn
from precursor.backend.services.agents import prompting
from precursor.backend.services.agents.manager import AgentManager
from precursor.backend.services.agents.timeline import Timeline, archive_event
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.llm.mock import MockProvider
from precursor.backend.services.mcp import precursor_server
from precursor.backend.services.mcp.precursor_tools import conversations
from precursor.backend.services.message_links import (
    MESSAGE_LINK_INSTRUCTION,
    discussion_path,
    message_path,
    message_reference_context,
)
from precursor.backend.services.turn_engine import apply_chat_system_prompt, hydrate_history


@pytest.mark.parametrize("kind", ["topics", "chats"])
def test_model_can_reference_a_previous_reply_and_streams_immediate_link_ids(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[ChatMessage] = []

    async def stream(
        _self: MockProvider,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        captured.extend(messages)
        note = next(
            m.content
            for m in messages
            if m.role == "system" and "## Recent conversation message references" in m.content
        )
        references = json.loads(
            note.split("## Recent conversation message references\n", 1)[1].split("\n", 1)[1]
        )
        link = next(r["href"] for r in references if r["role"] == "assistant")
        yield f"As explained in [the previous reply]({link}), use the stable link."

    async def provider(_session: Any) -> MockProvider:
        return MockProvider()

    monkeypatch.setattr(MockProvider, "stream_chat", stream)
    monkeypatch.setattr(conversation_turn, "get_llm_provider", provider)
    monkeypatch.setattr(conversations, "_section_enabled", AsyncMock(return_value=True))
    with TestClient(create_app()) as client:
        container = client.post(f"/api/{kind}", json={"title": "Citable replies"}).json()
        path = f"/api/{kind}/{container['id']}/messages"

        async def seed() -> None:
            async with SessionLocal() as session:
                # Exercise timestamp ties produced by coarse system clocks.
                session.add_all(
                    Message(
                        role=role,
                        content=text,
                        created_at=datetime(2026, 1, 1, tzinfo=UTC),
                        **{f"{kind[:-1]}_id": container["id"]},
                    )
                    for role, text in [
                        (MessageRole.USER, "How do links work?"),
                        (MessageRole.ASSISTANT, "They use immutable UUIDs."),
                    ]
                )
                await session.commit()

        asyncio.run(seed())
        previous = client.get(path).json()[-1]
        link = f"/{kind}/id/{container['public_id']}#message={previous['public_id']}"
        response = client.post(
            f"{path}/stream", json={"content": "Point back to your previous reply."}
        )
        assert response.status_code == 200
        assert MESSAGE_LINK_INSTRUCTION in captured[0].content
        assistant = next(m for m in captured if m.role == "assistant")
        assert assistant.content == "They use immutable UUIDs."
        assert assistant.permalink == link
        assert [m.content for m in captured if m.role == "user"] == [
            "How do links work?",
            "Point back to your previous reply.",
        ]
        assert all("[Message reference:" not in m.content for m in captured)
        rows = client.get(path).json()
        assert rows[1]["content"] == "They use immutable UUIDs."
        assert rows[-2]["content"] == "Point back to your previous reply."
        assert f"[the previous reply]({link})" in rows[-1]["content"]
        frames = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert any(frame.get("public_id") == rows[-2]["public_id"] for frame in frames)
        assert any(frame.get("public_id") == rows[-1]["public_id"] for frame in frames)

        async def transcript() -> dict[str, Any]:
            return await (
                precursor_server.list_messages(container["id"])
                if kind == "topics"
                else precursor_server.list_chat_messages(container["id"])
            )

        listed = asyncio.run(transcript())
        listed_previous = next(m for m in listed["messages"] if m["id"] == previous["id"])
        assert listed_previous["public_id"] == previous["public_id"]
        assert listed_previous["permalink"] == link


def test_message_references_preserve_compaction_skill_overrides_and_tool_pairing() -> None:
    def row(role: MessageRole, content: str, **kwargs: Any) -> Message:
        return Message(role=role, content=content, public_id=str(uuid4()), attachments=[], **kwargs)

    old = row(MessageRole.USER, "Old prompt")
    summary = row(MessageRole.SYSTEM, "Earlier discussion", kind=MESSAGE_KIND_COMPACTION)
    user = row(MessageRole.USER, "/to-en bravo")
    call = row(
        MessageRole.ASSISTANT,
        "",
        tool_calls=json.dumps([{"id": "call", "type": "function", "function": {"name": "read"}}]),
    )
    tool = row(MessageRole.TOOL, "Tool result", tool_calls='{"tool_call_id":"call","name":"read"}')
    reply = row(MessageRole.ASSISTANT, "Translated reply")
    path = discussion_path("topic", str(uuid4()))
    history = hydrate_history(
        [old, summary, user, call, tool, reply],
        discussion_path=path,
        prompt_override="Translate: bravo",
    )
    assert [m.role for m in history] == ["user", "assistant", "tool", "assistant"]
    assert "Earlier discussion" in history[0].content
    assert history[0].permalink == message_path(path, user.public_id)
    assert history[0].content.endswith("Translate: bravo")
    assert history[1].content == ""
    assert history[1].tool_calls[0]["id"] == history[2].tool_call_id == "call"
    assert history[2].content == "Tool result"
    assert history[3].content == "Translated reply"
    assert history[3].permalink == message_path(path, reply.public_id)
    references = message_reference_context(history)
    assert message_path(path, old.public_id) not in references
    assert message_path(path, user.public_id) in references
    assert message_path(path, reply.public_id) in references
    assert user.content == "/to-en bravo"


def test_reference_index_is_bounded_and_leaves_message_text_untouched() -> None:
    history = [
        ChatMessage(
            role="assistant",
            content=f"Reply {i}: " + "x" * 300,
            permalink=f"/topics/id/topic#message={i}",
        )
        for i in range(55)
    ]
    history.extend(
        [
            ChatMessage(role="assistant", content="", permalink="/empty"),
            ChatMessage(role="tool", content="Tool output", permalink="/tool"),
            ChatMessage(role="user", content="No reference"),
        ]
    )
    context = message_reference_context(history)
    references = json.loads(context.split("\n", 2)[2])
    assert len(references) == 50
    assert references[0]["excerpt"].startswith("Reply 5:")
    assert references[-1]["href"] == "/topics/id/topic#message=54"
    assert all(len(r["excerpt"]) <= 240 for r in references)
    assert history[0].content == "Reply 0: " + "x" * 300
    assert message_reference_context([]) == ""
    assert message_reference_context([ChatMessage(role="user", content="Hello")]) == ""


def test_chat_system_instruction_keeps_navigation_metadata_separate() -> None:
    chat = Chat(description="Answer in French", description_as_system_prompt=True)
    original = ChatMessage(
        role="user",
        content="Hello",
        image_urls=["data:image/png;base64,abc"],
        permalink="/chats/id/chat#message=prompt",
    )
    history = apply_chat_system_prompt(chat, [original])
    assert history[0].content.endswith("\n\nHello")
    assert original.permalink not in history[0].content
    assert history[0].permalink == original.permalink
    assert history[0].image_urls == original.image_urls
    assert original.content == "Hello"
    assert original.permalink in message_reference_context(history)


@pytest.mark.parametrize("kind", ["topics", "chats"])
def test_echoing_provider_does_not_receive_a_reference_header(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stream(
        _self: MockProvider,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        yield next(m.content for m in reversed(messages) if m.role == "user")

    async def provider(_session: Any) -> MockProvider:
        return MockProvider()

    monkeypatch.setattr(MockProvider, "stream_chat", stream)
    monkeypatch.setattr(conversation_turn, "get_llm_provider", provider)
    with TestClient(create_app()) as client:
        container = client.post(f"/api/{kind}", json={"title": "No reference headers"}).json()
        path = f"/api/{kind}/{container['id']}/messages"
        for content in ("First prompt", "Another prompt"):
            response = client.post(f"{path}/stream", json={"content": content})
            assert response.status_code == 200
            assert "[Message reference:" not in response.text
        assert [m["content"] for m in client.get(path).json()] == [
            "First prompt",
            "First prompt",
            "Another prompt",
            "Another prompt",
        ]


def test_agent_reference_context_is_bounded_and_isolated_to_its_execution() -> None:
    agent = AgentSession(public_id=str(uuid4()), autonomy_enabled=True)
    events = [
        AgentEvent(
            kind="assistant_message",
            text=f"Reply {i}: " + "x" * 300,
            agent_run_id=1,
            link_id=str(uuid4()),
        )
        for i in range(55)
    ]
    events.extend(
        [
            AgentEvent(
                kind="assistant_message",
                text="Other execution",
                agent_run_id=2,
                link_id=str(uuid4()),
            ),
            AgentEvent(kind="assistant_delta", text="Not persisted", agent_run_id=1),
            AgentEvent(
                kind="assistant_message", text="PROGRESS: 50", agent_run_id=1, link_id=str(uuid4())
            ),
            AgentEvent(
                kind="UserMessageData", text="Human follow-up", agent_run_id=1, link_id=str(uuid4())
            ),
        ]
    )
    context = prompting.message_references(agent, events, 1)
    references = json.loads(context.split("\n", 2)[2])
    assert len(references) == 50
    assert references[0]["excerpt"].startswith("Reply 6:")
    assert all(len(reference["excerpt"]) <= 240 for reference in references)
    assert references[-1]["role"] == "user"
    assert references[-1]["href"] == message_path(
        discussion_path("agent", agent.public_id), events[-1].link_id, agent=True
    )
    assert "Other execution" not in context and "PROGRESS:" not in context
    assert prompting.message_references(agent, [], 1) == ""


def test_agent_hook_supplies_fresh_links_without_modifying_the_prompt() -> None:
    with TestClient(create_app()):

        async def check() -> None:
            async with SessionLocal() as session:
                agent = AgentSession(title="References", task_prompt="Link replies")
                session.add(agent)
                await session.flush()
                run = AgentRun(agent_id=agent.id, model="auto", use_mcp=False)
                session.add(run)
                await session.commit()
            manager = AgentManager()
            captured: dict[str, Any] = {}

            class Stop(Exception):
                pass

            class Client:
                async def create_session(self, **kwargs: Any) -> None:
                    captured.update(kwargs)
                    raise Stop

            manager._client = Client()  # type: ignore[assignment]
            with pytest.raises(Stop):
                await manager._ensure_live_locked(agent, run)
            assert MESSAGE_LINK_INSTRUCTION in captured["system_message"]["content"]
            hook = captured["hooks"]["on_user_prompt_submitted"]
            request = {"prompt": "Refer to an earlier reply."}
            assert await hook(request, {}) is None
            event = AgentEvent(
                kind="assistant_message", text="An archived answer.", agent_run_id=run.id
            )
            await manager._record(agent.id, event)
            context = await hook(request, {})
            assert set(context) == {"additionalContext"}
            assert (
                message_path(discussion_path("agent", agent.public_id), event.link_id, agent=True)
                in context["additionalContext"]
            )
            assert request == {"prompt": "Refer to an earlier reply."}
            assert event.text == "An archived answer."

        asyncio.run(check())


def test_chat_permalink_survives_slug_changes_and_archiving() -> None:
    with TestClient(create_app()) as client:
        chat = client.post("/api/chats", json={"title": "Permalink chat"}).json()
        public_id = chat["public_id"]
        assert str(UUID(public_id)) == public_id
        url = f"/api/chats/by-public-id/{public_id}"
        client.patch(f"/api/chats/{chat['id']}", json={"slug": "new-permalink-chat"})
        resolved = client.get(url)
        assert resolved.status_code == 200
        assert resolved.json()["id"] == chat["id"]
        assert resolved.json()["slug"] == "new-permalink-chat"
        client.post(f"/api/chats/{chat['id']}/archive")
        assert client.get(url).json()["archived_at"] is not None
        client.delete(f"/api/chats/{chat['id']}")
        assert client.get(url).status_code == 404


def test_message_public_ids_survive_edits_and_are_not_reused() -> None:
    with TestClient(create_app()) as client:
        topic = client.post("/api/topics", json={"title": "Message identities"}).json()
        chat = client.post("/api/chats", json={"title": "Chat identities"}).json()

        async def seed() -> None:
            async with SessionLocal() as session:
                session.add_all(
                    [
                        Message(topic_id=topic["id"], role=MessageRole.ASSISTANT, content="Reply"),
                        Message(chat_id=chat["id"], role=MessageRole.ASSISTANT, content="Reply"),
                    ]
                )
                await session.commit()

        asyncio.run(seed())
        identities: set[str] = set()
        for kind, container in (("topics", topic), ("chats", chat)):
            path = f"/api/{kind}/{container['id']}/messages"
            message = client.get(path).json()[0]
            public_id = message["public_id"]
            assert str(UUID(public_id)) == public_id
            assert public_id not in identities
            identities.add(public_id)
            edited = client.patch(f"{path}/{message['id']}", json={"content": "Edited"})
            assert edited.status_code == 200, edited.text
            assert edited.json()["public_id"] == public_id
            assert client.delete(f"{path}/{message['id']}").status_code == 204
        asyncio.run(seed())
        for kind, container in (("topics", topic), ("chats", chat)):
            message = client.get(f"/api/{kind}/{container['id']}/messages").json()[0]
            assert message["public_id"] not in identities


def test_agent_links_survive_archive_reload_including_legacy_events() -> None:
    with TestClient(create_app()):

        async def check() -> None:
            async with SessionLocal() as session:
                agent = AgentSession(title="Event links", task_prompt="Test")
                session.add(agent)
                await session.flush()
                session.add(
                    AgentEventRecord(
                        agent_session_id=agent.id,
                        payload='{"kind":"assistant","text":"Legacy reply"}',
                    )
                )
                await session.commit()
                agent_id = agent.id
            event = AgentEvent(kind="assistant", text="New reply")
            await archive_event(agent_id, event)
            assert event.link_id is not None

            async def reload() -> list[AgentEvent]:
                manager = SimpleNamespace(_loaded=set(), _events_lock=asyncio.Lock(), _events={})
                await Timeline(manager).ensure_loaded(agent_id)
                return manager._events[agent_id]

            first = await reload()
            second = await reload()
            assert len(first) == 2
            assert first[0].link_id is not None
            assert first[1].link_id == event.link_id
            assert [e.link_id for e in first] == [e.link_id for e in second]
            assert len({e.link_id for e in first}) == 2
            assert all(e.event_id is None for e in first)  # Not SDK rewind ids.

        asyncio.run(check())


def test_permalink_migration_backfills_unique_ids_without_changing_content() -> None:
    migration = importlib.import_module(
        "precursor.backend.alembic.versions.5fa65fad981a_message_and_chat_permalink_identities"
    )
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            for table in ("chats", "messages"):
                connection.execute(
                    sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, content TEXT)")
                )
                connection.execute(
                    sa.text(f"INSERT INTO {table} (id, content) VALUES (:id, :content)"),
                    [{"id": i, "content": f"Original {i}"} for i in range(1, 1003)],
                )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            ids: set[str] = set()
            for table in ("chats", "messages"):
                rows = connection.execute(
                    sa.text(f"SELECT id, content, public_id FROM {table}")
                ).all()
                assert len(rows) == 1002
                for row_id, content, public_id in rows:
                    assert content == f"Original {row_id}"
                    assert str(UUID(public_id)) == public_id
                    assert public_id not in ids
                    ids.add(public_id)
                columns = sa.inspect(connection).get_columns(table)
                assert not next(c for c in columns if c["name"] == "public_id")["nullable"]
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            for table in ("chats", "messages"):
                assert "public_id" not in {
                    c["name"] for c in sa.inspect(connection).get_columns(table)
                }
                assert connection.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 1002
    finally:
        engine.dispose()
