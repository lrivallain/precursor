"""Self wakeups: a model pauses its conversation and is woken up later."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentSession, Chat, Topic
from precursor.backend.services import events, wakeup_tools, wakeups
from precursor.backend.services.mcp.precursor_tools import wakeups as mcp_wakeups
from precursor.backend.services.wakeup_ticker import WakeupTicker

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


async def _topic(title: str = "Waiting topic") -> int:
    async with SessionLocal() as session:
        topic = Topic(title=title, slug=f"wake-{datetime.now(UTC).timestamp()}")
        session.add(topic)
        await session.commit()
        return topic.id


async def _make_due(kind: wakeups.WakeupKind, target_id: int) -> None:
    async with SessionLocal() as session:
        row = await wakeups.get_wakeup(session, kind, target_id)
        assert row is not None
        row.wake_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


# -- when ------------------------------------------------------------------


def test_wake_at_from_a_delay() -> None:
    assert wakeups.resolve_wake_at(delay_seconds=600, at=None, now=NOW) == NOW + timedelta(
        minutes=10
    )


def test_wake_at_from_an_iso_time_naive_is_utc() -> None:
    got = wakeups.resolve_wake_at(delay_seconds=None, at="2026-11-25T14:30:00", now=NOW)
    assert got == datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
    got = wakeups.resolve_wake_at(delay_seconds=None, at="2026-11-25T14:30:00+01:00", now=NOW)
    assert got == datetime(2026, 11, 25, 13, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("delay", "at", "message"),
    [
        (None, None, "exactly one"),
        (60, "2026-11-25T14:30:00Z", "exactly one"),
        (2, None, "at least"),
        (31 * 86_400, None, "at most"),
        (None, "next tuesday", "ISO 8601"),
        (None, "2026-11-25T11:00:00Z", "at least"),
    ],
)
def test_wake_at_is_refused(delay: float | None, at: str | None, message: str) -> None:
    with pytest.raises(wakeups.WakeupError, match=message):
        wakeups.resolve_wake_at(delay_seconds=delay, at=at, now=NOW)


# -- the turn's built-in tools -------------------------------------------------


async def test_turn_tools_pause_replace_and_cancel_their_own_conversation() -> None:
    topic_id = await _topic()
    tools, run = wakeup_tools.local_tools("topic", topic_id)
    assert [t.name for t in tools] == ["schedule_wakeup", "cancel_wakeup"]

    def call(name: str, **arguments: Any) -> SimpleNamespace:
        return SimpleNamespace(name=name, arguments=json.dumps(arguments))

    result = json.loads(
        await run(call("schedule_wakeup", prompt="Check the CI", delay_seconds=600, reason="CI"))
    )
    assert result["scheduled"] is True
    assert (result["kind"], result["target_id"]) == ("topic", topic_id)

    # Scheduling again replaces: one wakeup per conversation.
    await run(call("schedule_wakeup", prompt="Check the deploy", delay_seconds="120"))
    async with SessionLocal() as session:
        pending = [w for w in await wakeups.list_pending(session) if w.target_id == topic_id]
    assert [(w.kind, w.prompt, w.reason, w.title) for w in pending] == [
        ("topic", "Check the deploy", None, "Waiting topic")
    ]

    assert json.loads(await run(call("cancel_wakeup"))) == {"cancelled": True}
    assert json.loads(await run(call("cancel_wakeup"))) == {"cancelled": False}


async def test_turn_tools_report_bad_arguments_to_the_model() -> None:
    topic_id = await _topic()
    _tools, run = wakeup_tools.local_tools("topic", topic_id)
    bad = SimpleNamespace(name="schedule_wakeup", arguments='{"prompt": " ", "delay_seconds": 60}')
    assert "must not be empty" in json.loads(await run(bad))["error"]
    bad = SimpleNamespace(name="schedule_wakeup", arguments='{"prompt": "x", "delay_seconds": "x"}')
    assert "error" in json.loads(await run(bad))
    bad = SimpleNamespace(name="schedule_wakeup", arguments="not json")
    assert "error" in json.loads(await run(bad))


# -- the ticker ------------------------------------------------------------------


async def test_ticker_wakes_a_due_topic_with_its_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    topic_id = await _topic()
    async with SessionLocal() as session:
        await wakeups.schedule(
            session, "topic", topic_id, wake_at=NOW + timedelta(days=1), prompt="Check the CI"
        )
    await _make_due("topic", topic_id)

    started: list[tuple[Any, ...]] = []

    async def fake_turn(kind: str, container_id: int, prompt: str, **kw: Any) -> None:
        started.append((kind, container_id, prompt, kw.get("message_kind")))

    monkeypatch.setattr("precursor.backend.services.turn.run_container_turn", fake_turn)
    ticker = WakeupTicker()
    await ticker.run_once()
    for task in list(ticker._turns):
        await task

    assert len(started) == 1
    kind, container_id, prompt, message_kind = started[0]
    assert (kind, container_id, message_kind) == ("topic", topic_id, "wakeup")
    assert prompt.startswith("⏰ Self wake-up") and prompt.endswith("Check the CI")
    async with SessionLocal() as session:
        assert await wakeups.get_wakeup(session, "topic", topic_id) is None


async def test_ticker_waits_while_a_turn_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    topic_id = await _topic()
    async with SessionLocal() as session:
        await wakeups.schedule(
            session, "topic", topic_id, wake_at=NOW + timedelta(days=1), prompt="Later"
        )
    await _make_due("topic", topic_id)
    monkeypatch.setattr(events, "_live_streams", {("topic", topic_id): 1})
    started: list[int] = []

    async def fake_turn(*_a: Any, **_kw: Any) -> None:
        started.append(1)

    monkeypatch.setattr("precursor.backend.services.turn.run_container_turn", fake_turn)
    await WakeupTicker().run_once()

    assert started == []
    async with SessionLocal() as session:
        assert await wakeups.get_wakeup(session, "topic", topic_id) is not None


async def test_ticker_sends_an_agent_its_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    async with SessionLocal() as session:
        agent = AgentSession(title="Waiter", task_prompt="x", status="completed")
        session.add(agent)
        await session.commit()
        agent_id, public_id = agent.id, agent.public_id

    async with SessionLocal() as session:
        assert await wakeups.resolve_public_id(session, public_id) == ("agent", agent_id)
        await wakeups.schedule(
            session, "agent", agent_id, wake_at=NOW + timedelta(days=1), prompt="Go on"
        )
    await _make_due("agent", agent_id)

    sent: list[tuple[int, str]] = []

    async def send_message(target: int, text: str) -> None:
        sent.append((target, text))

    async def run(coro: Any) -> None:
        await coro

    pending: list[Any] = []
    manager = SimpleNamespace(
        ready=True, send_message=send_message, enqueue=lambda coro: pending.append(coro)
    )
    monkeypatch.setattr(
        "precursor.backend.services.agents.manager.get_agent_manager", lambda: manager
    )
    await WakeupTicker().run_once()
    for coro in pending:
        await run(coro)

    assert [(t, text.endswith("Go on")) for t, text in sent] == [(agent_id, True)]


# -- the MCP tools (agents) ------------------------------------------------------


async def test_mcp_tools_resolve_the_conversation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRECURSOR_MCP_FULL_ACCESS", "1")
    async with SessionLocal() as session:
        chat = Chat(title="Chat that waits", slug=f"c-{datetime.now(UTC).timestamp()}")
        session.add(chat)
        await session.commit()
        chat_id, public_id = chat.id, chat.public_id

    result = await mcp_wakeups.schedule_wakeup(public_id, "Look again", delay_seconds=300)
    assert (result["kind"], result["target_id"]) == ("chat", chat_id)
    assert await mcp_wakeups.cancel_wakeup(public_id) == {"cancelled": True}
    assert "No topic, chat or agent" in (await mcp_wakeups.cancel_wakeup("nope"))["error"]


async def test_mcp_tools_honour_the_expose_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRECURSOR_MCP_FULL_ACCESS", raising=False)
    result = await mcp_wakeups.cancel_wakeup("whatever")
    assert "not exposed" in result["error"]


# -- the API -----------------------------------------------------------------------


def test_api_lists_wakes_and_cancels() -> None:
    import asyncio

    with TestClient(create_app()) as client:
        topic_id = client.post("/api/topics", json={"title": "Paused topic"}).json()["id"]

        async def schedule() -> int:
            async with SessionLocal() as session:
                row = await wakeups.schedule(
                    session,
                    "topic",
                    topic_id,
                    wake_at=datetime.now(UTC) + timedelta(hours=1),
                    prompt="Check",
                    reason="Waiting for the build",
                )
                return row.id

        wakeup_id = asyncio.run(schedule())
        listed = [w for w in client.get("/api/wakeups").json() if w["id"] == wakeup_id]
        assert listed[0]["kind"] == "topic"
        assert listed[0]["title"] == "Paused topic"
        assert listed[0]["reason"] == "Waiting for the build"

        assert client.delete(f"/api/wakeups/{wakeup_id}").status_code == 204
        assert client.delete(f"/api/wakeups/{wakeup_id}").status_code == 404
        assert client.post(f"/api/wakeups/{wakeup_id}/wake").status_code == 404


async def test_wakeup_changed_reaches_the_window_whose_turn_paused() -> None:
    """The model pauses inside a turn a window started: that window must hear it too."""
    events.set_current_client_id("the-window")
    try:
        async with events.get_bus().subscribe() as queue:
            await events.publish_wakeup_changed()
            event = queue.get_nowait()
    finally:
        events.set_current_client_id(None)
    assert event["type"] == "wakeup.changed"
    assert not event.get("client_id")
