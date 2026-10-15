"""Approvals raised by unattended (scheduled) runs: deadlines, auto-deny, headless resume."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import Message, MessageRole, Topic
from precursor.backend.models.tool_approval import (
    APPROVAL_DENIED,
    APPROVAL_PENDING,
    ToolApproval,
)
from precursor.backend.models.topic_schedule import TopicSchedule
from precursor.backend.services import unattended_approvals
from precursor.backend.services.llm.base import ProviderEvent, ToolCallsEvent, TurnDoneEvent
from precursor.backend.services.mcp.client import MCPToolDef
from precursor.backend.services.turn import run_topic_turn
from precursor.backend.services.unattended_approvals import (
    expire_overdue,
    resume_headless,
)
from tests.test_topic_turn import (
    _answer,
    _Call,
    _install_mcp,
    _install_provider,
    _ScriptedProvider,
    enabled_servers,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("enabled_servers")

_WRITE = MCPToolDef(
    server="acme",
    name="publish",
    description="Publish something",
    input_schema={"type": "object", "properties": {}},
)


def _publish_round() -> list[ProviderEvent]:
    return [
        ToolCallsEvent(calls=[_Call("w1", "acme__publish")]),  # type: ignore[list-item]
        TurnDoneEvent(finish_reason="tool_calls"),
    ]


async def _topic(*, timeout: int | None = None, scheduled: bool = False) -> int:
    await init_db()
    async with SessionLocal() as session:
        topic = Topic(
            title="Nightly", slug=f"unattended-{uuid4().hex[:8]}", approval_policy="manual"
        )
        session.add(topic)
        await session.flush()
        if scheduled:
            session.add(
                TopicSchedule(
                    topic_id=topic.id,
                    prompt="go",
                    interval_seconds=3600,
                    approval_timeout_minutes=timeout,
                )
            )
        await session.commit()
        return topic.id


async def _approvals(topic_id: int) -> list[ToolApproval]:
    async with SessionLocal() as session:
        rows = await session.execute(select(ToolApproval).where(ToolApproval.topic_id == topic_id))
        return list(rows.scalars().all())


async def _expire_now(topic_id: int) -> None:
    async with SessionLocal() as session:
        for row in (
            await session.execute(select(ToolApproval).where(ToolApproval.topic_id == topic_id))
        ).scalars():
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()


@pytest.fixture
def parked_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def capture(**kwargs: Any) -> None:
        seen.append(kwargs)

    monkeypatch.setattr(unattended_approvals, "publish_approval_parked", capture)
    return seen


async def test_parked_scheduled_run_gets_the_default_deadline_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
    parked_events: list[dict[str, Any]],
) -> None:
    _install_provider(monkeypatch, _ScriptedProvider(_publish_round()))
    _install_mcp(monkeypatch, tools=[_WRITE])
    topic_id = await _topic()

    await run_topic_turn(topic_id, "go")

    (row,) = await _approvals(topic_id)
    assert row.status == APPROVAL_PENDING and row.expires_at is not None
    left = row.expires_at.replace(tzinfo=UTC) - datetime.now(UTC)
    assert timedelta(minutes=58) < left <= timedelta(minutes=60)
    assert [(e["topic_id"], e["title"], e["count"]) for e in parked_events] == [
        (topic_id, "Nightly", 1)
    ]


@pytest.mark.parametrize(("override", "minutes"), [(5, 5), (0, None)])
async def test_schedule_override_sets_or_disables_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
    parked_events: list[dict[str, Any]],
    override: int,
    minutes: int | None,
) -> None:
    _ = parked_events
    _install_provider(monkeypatch, _ScriptedProvider(_publish_round()))
    _install_mcp(monkeypatch, tools=[_WRITE])
    topic_id = await _topic(timeout=override, scheduled=True)

    await run_topic_turn(topic_id, "go")

    (row,) = await _approvals(topic_id)
    if minutes is None:
        assert row.expires_at is None
    else:
        assert row.expires_at is not None
        left = row.expires_at.replace(tzinfo=UTC) - datetime.now(UTC)
        assert timedelta(minutes=minutes - 2) < left <= timedelta(minutes=minutes)


async def test_expired_call_is_denied_and_the_run_finishes_headlessly(
    monkeypatch: pytest.MonkeyPatch,
    parked_events: list[dict[str, Any]],
) -> None:
    _ = parked_events
    provider = _ScriptedProvider(_publish_round(), _answer("Could not publish; nobody answered."))
    _install_provider(monkeypatch, provider)
    executed: list[str] = []

    async def handler(server: str, raw_name: str, args: dict[str, Any]) -> Any:
        executed.append(raw_name)

    _install_mcp(monkeypatch, tools=[_WRITE], handler=handler)
    topic_id = await _topic()
    await run_topic_turn(topic_id, "go")

    assert topic_id not in await expire_overdue()  # not due yet
    await _expire_now(topic_id)
    assert topic_id in await expire_overdue()

    (row,) = await _approvals(topic_id)
    assert row.status == APPROVAL_DENIED
    assert row.feedback and "Nobody answered" in row.feedback

    await resume_headless(topic_id)

    assert executed == []
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Message).where(Message.topic_id == topic_id).order_by(Message.id)
                )
            )
            .scalars()
            .all()
        )
    assert [m.role for m in rows] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert "Nobody answered" in rows[2].content
    assert rows[3].content == "Could not publish; nobody answered."
    assert all(r.executed for r in await _approvals(topic_id))


async def test_interactive_approvals_never_expire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic_id = await _topic()
    async with SessionLocal() as session:
        session.add(
            ToolApproval(
                topic_id=topic_id,
                tool_call_id="c1",
                tool_name="acme__publish",
                server="acme",
                raw_tool="publish",
                arguments="{}",
                kind="tool",
                status=APPROVAL_PENDING,
                scope="once",
                executed=False,
            )
        )
        await session.commit()

    # Other tests leave stamped rows behind, so only this topic is asserted on.
    assert topic_id not in await expire_overdue(datetime.now(UTC) + timedelta(days=365))
    (row,) = await _approvals(topic_id)
    assert row.status == APPROVAL_PENDING


async def test_parked_event_reaches_subscribers_with_its_payload() -> None:
    from precursor.backend.services.events import get_bus, publish_approval_parked

    deadline = datetime(2030, 1, 2, 3, 4, tzinfo=UTC)
    async with get_bus().subscribe() as queue:
        await publish_approval_parked(topic_id=7, title="Nightly", count=2, expires_at=deadline)
        event = queue.get_nowait()

    assert event["type"] == "approval.parked"
    assert (event["topic_id"], event["title"], event["count"]) == (7, "Nightly", 2)
    assert event["expires_at"] == deadline.isoformat()


class _ToolSpy(_ScriptedProvider):
    """Records the tool names each round was offered."""

    def __init__(self, *rounds: list[ProviderEvent]) -> None:
        super().__init__(*rounds)
        self.offered: list[list[str]] = []

    async def stream_chat_with_tools(self, *, tools: Any, **kwargs: Any) -> Any:
        self.offered.append(sorted(t.name for t in tools))
        async for event in super().stream_chat_with_tools(tools=tools, **kwargs):
            yield event


async def test_headless_resume_offers_the_same_tools_as_the_scheduled_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToolSpy(_publish_round(), _answer("done"))
    _install_provider(monkeypatch, spy)
    own = [
        MCPToolDef(
            server="precursor",
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}},
        )
        for name in ("post_message", "read_workspace_file")
    ]
    _install_mcp(monkeypatch, tools=[_WRITE, *own])
    topic_id = await _topic()
    await run_topic_turn(topic_id, "go")
    await _expire_now(topic_id)
    await expire_overdue()
    await resume_headless(topic_id)

    first, resumed = spy.offered
    assert first == resumed
    assert not any("post_message" in name for name in resumed)
    assert any("read_workspace_file" in name for name in resumed)
