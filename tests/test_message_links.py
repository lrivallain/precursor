"""Durable identities used by discussion message permalinks."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from uuid import UUID

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentEventRecord, AgentSession, Message, MessageRole
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents.timeline import Timeline, archive_event


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
