"""Tests for age-limited retention of TOOL message results.

The sweep replaces the ``content`` of aged TOOL rows with a short placeholder
*in place* — keeping the row and its ``tool_calls`` metadata so
``_hydrate_history`` still pairs each assistant tool-call turn with its TOOL
rows. Covers: disabled (0) is a no-op; old large TOOL rows get the placeholder
while recent/non-TOOL rows are untouched; idempotency; and that a pruned row
still hydrates without dropping its assistant turn.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting, Message, MessageRole, Topic
from precursor.backend.services.tool_result_retention import (
    PRUNED_PLACEHOLDER,
    prune_expired_tool_results,
)
from precursor.backend.services.turn_engine import hydrate_history


def _init_db() -> None:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass


async def _set_retention(days: int) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "tool_result_retention_days")
        encoded = json.dumps(days)
        if row is None:
            session.add(AppSetting(key="tool_result_retention_days", value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def _seed(topic_title: str) -> int:
    """Create a topic with an assistant tool-call turn + old/new TOOL rows."""
    from sqlalchemy import delete

    old = datetime.now(UTC) - timedelta(days=40)
    recent = datetime.now(UTC) - timedelta(days=1)
    async with SessionLocal() as session:
        # Isolate from other tests sharing the session-wide temp DB — the sweep
        # is global, so stray old TOOL rows would skew the affected counts.
        await session.execute(delete(Message))
        await session.execute(delete(Topic))
        topic = Topic(title=topic_title, slug=topic_title)
        session.add(topic)
        await session.flush()
        tid = topic.id

        assistant = Message(
            topic_id=tid,
            role=MessageRole.ASSISTANT,
            content="calling tools",
            tool_calls=json.dumps([{"id": "call_1"}, {"id": "call_2"}]),
            created_at=old,
        )
        old_tool = Message(
            topic_id=tid,
            role=MessageRole.TOOL,
            content="X" * 1000,
            tool_calls=json.dumps({"tool_call_id": "call_1", "name": "search"}),
            created_at=old,
        )
        recent_tool = Message(
            topic_id=tid,
            role=MessageRole.TOOL,
            content="Y" * 1000,
            tool_calls=json.dumps({"tool_call_id": "call_2", "name": "fetch"}),
            created_at=recent,
        )
        old_user = Message(
            topic_id=tid,
            role=MessageRole.USER,
            content="Z" * 1000,
            created_at=old,
        )
        session.add_all([assistant, old_tool, recent_tool, old_user])
        await session.commit()
    return tid


async def _content_by_role(tid: int) -> dict[str, list[str]]:
    from sqlalchemy import select

    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(Message).where(Message.topic_id == tid))).scalars().all()
        )
        out: dict[str, list[str]] = {}
        for m in rows:
            out.setdefault(m.role.value, []).append(m.content)
    return out


def test_retention_disabled_is_noop() -> None:
    _init_db()

    async def _run() -> None:
        await _set_retention(0)
        tid = await _seed("disabled")
        assert await prune_expired_tool_results() == 0
        by_role = await _content_by_role(tid)
        assert all(c != PRUNED_PLACEHOLDER for c in by_role["tool"])

    asyncio.run(_run())


def test_prunes_old_tool_rows_only() -> None:
    _init_db()

    async def _run() -> None:
        await _set_retention(30)
        tid = await _seed("prune")
        assert await prune_expired_tool_results() == 1
        by_role = await _content_by_role(tid)
        # Old TOOL row got the placeholder; recent TOOL row untouched.
        assert PRUNED_PLACEHOLDER in by_role["tool"]
        assert any(c == "Y" * 1000 for c in by_role["tool"])
        # Non-TOOL rows are never touched even when old + large.
        assert by_role["user"] == ["Z" * 1000]
        assert by_role["assistant"] == ["calling tools"]

    asyncio.run(_run())


def test_idempotent_second_run() -> None:
    _init_db()

    async def _run() -> None:
        await _set_retention(30)
        await _seed("idempotent")
        assert await prune_expired_tool_results() == 1
        assert await prune_expired_tool_results() == 0

    asyncio.run(_run())


def test_pruned_row_still_hydrates_pairing() -> None:
    _init_db()

    async def _run() -> list[object]:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        await _set_retention(30)
        tid = await _seed("pairing")
        await prune_expired_tool_results()
        async with SessionLocal() as session:
            rows = list(
                (
                    await session.execute(
                        select(Message)
                        .where(Message.topic_id == tid)
                        .options(selectinload(Message.attachments))
                        .order_by(Message.id)
                    )
                )
                .scalars()
                .all()
            )
            return hydrate_history(rows)

    hydrated = asyncio.run(_run())
    roles = [m.role for m in hydrated]  # type: ignore[attr-defined]
    # The assistant tool-call turn survives with both tool results paired.
    assert roles.count("assistant") == 1
    assert roles.count("tool") == 2
    tool_contents = [m.content for m in hydrated if m.role == "tool"]  # type: ignore[attr-defined]
    assert PRUNED_PLACEHOLDER in tool_contents
