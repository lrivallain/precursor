"""Scheduled-command dispatch tests.

Scheduled prompts that begin with a slash command should run the command's
backend action headlessly instead of being forwarded to the LLM. These tests
drive the orchestrator directly (the scheduler is a thin wrapper around it).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import Message
from precursor.backend.services import scheduled_commands as sc


def _make_topic(client: TestClient, title: str = "Scheduled") -> int:
    created = client.post("/api/topics", json={"title": title})
    assert created.status_code in (200, 201)
    return created.json()["id"]


def _messages(client: TestClient, topic_id: int) -> list[dict[str, object]]:
    return client.get(f"/api/topics/{topic_id}/messages").json()


def _run_prompt(topic_id: int, prompt: str, *, clear_context: bool = False) -> None:
    asyncio.run(sc.run_scheduled_prompt(topic_id, prompt, clear_context=clear_context))


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------


def test_parse_command_recognises_slash_commands() -> None:
    assert sc.parse_command("/agent run tests") == ("agent", "run tests")
    assert sc.parse_command("  /gh-sync  ") == ("gh-sync", "")
    assert sc.parse_command("/Rename New Title") == ("rename", "New Title")


def test_parse_command_ignores_plain_text() -> None:
    assert sc.parse_command("hello there") is None
    assert sc.parse_command("") is None
    assert sc.parse_command("not /a command") is None


# ---------------------------------------------------------------------------
# Plain prompts still go to the LLM turn
# ---------------------------------------------------------------------------


def test_plain_prompt_runs_normal_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)

        calls: list[tuple[int, str, str | None]] = []

        async def fake_turn(tid, prompt, *, clear_context=False, llm_prompt=None):  # type: ignore[no-untyped-def]
            calls.append((tid, prompt, llm_prompt))

        monkeypatch.setattr(sc, "run_topic_turn", fake_turn)
        _run_prompt(topic_id, "Summarise the latest changes")

        assert calls == [(topic_id, "Summarise the latest changes", None)]


def test_unknown_command_falls_through_to_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)

        calls: list[str] = []

        async def fake_turn(tid, prompt, *, clear_context=False, llm_prompt=None):  # type: ignore[no-untyped-def]
            calls.append(prompt)

        monkeypatch.setattr(sc, "run_topic_turn", fake_turn)
        _run_prompt(topic_id, "/totallyunknown do a thing")

        assert calls == ["/totallyunknown do a thing"]


# ---------------------------------------------------------------------------
# Built-in commands run their backend action (not the LLM)
# ---------------------------------------------------------------------------


def test_rename_command_renames_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client, "Old name")

        async def fail_turn(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a command must not invoke the LLM turn")

        monkeypatch.setattr(sc, "run_topic_turn", fail_turn)
        _run_prompt(topic_id, "/rename Brand New Name")

        assert client.get(f"/api/topics/{topic_id}").json()["title"] == "Brand New Name"
        assert any("Brand New Name" in m["content"] for m in _messages(client, topic_id))


def test_pin_command_pins_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)

        async def fail_turn(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a command must not invoke the LLM turn")

        monkeypatch.setattr(sc, "run_topic_turn", fail_turn)
        _run_prompt(topic_id, "/pin")

        assert client.get(f"/api/topics/{topic_id}").json()["pinned"] is True


def test_clear_command_wipes_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        # Seed a message so there's something to clear.
        asyncio.run(_seed_message(topic_id, "leftover"))
        assert _messages(client, topic_id)

        _run_prompt(topic_id, "/clear")

        msgs = _messages(client, topic_id)
        # Only the receipt remains; the seeded message is gone.
        assert all(m["content"] != "leftover" for m in msgs)
        assert any("Cleared" in m["content"] for m in msgs)


def test_agent_command_is_dispatched_not_sent_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)

        async def fail_turn(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("/agent must not invoke the LLM turn")

        monkeypatch.setattr(sc, "run_topic_turn", fail_turn)
        # Agents mode is disabled by default → the reused router raises, and the
        # failure is surfaced in-chat rather than crashing the schedule.
        _run_prompt(topic_id, "/agent run the smoke tests")

        receipts = [m["content"] for m in _messages(client, topic_id)]
        assert any(c.startswith("`/agent` failed:") for c in receipts)


def test_split_agent_directive() -> None:
    assert sc._split_agent_directive("/clear poll the inbox") == ("clear", "poll the inbox")
    assert sc._split_agent_directive("  /Clear   ") == ("clear", "")
    assert sc._split_agent_directive("/run") == ("run", "")
    assert sc._split_agent_directive("/run focus on FR mail") == ("run", "focus on FR mail")
    assert sc._split_agent_directive("just a message") == (None, "just a message")
    # A bare "/cleared" / "/running" is not a directive (word boundary).
    assert sc._split_agent_directive("/cleared up") == (None, "/cleared up")
    assert sc._split_agent_directive("/running late") == (None, "/running late")


def test_agent_clear_directive_resets_then_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/agent <uuid> /clear <prompt>` wipes the agent's context (same uuid) and
    then sends the remaining prompt as a fresh turn."""
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        agent_uuid = "11111111-1111-1111-1111-111111111111"
        agent_id = asyncio.run(_seed_agent(agent_uuid, title="Inbox watcher"))

        calls: dict[str, object] = {}

        async def fake_clear(self, aid, *, keep_id=False):  # type: ignore[no-untyped-def]
            calls["clear"] = (aid, keep_id)

        async def fake_send(agent_ref, payload, session):  # type: ignore[no-untyped-def]
            calls["send"] = payload.message

        from precursor.backend.routers import agents as agents_router
        from precursor.backend.services.agents import manager as mgr_mod

        monkeypatch.setattr(mgr_mod.AgentManager, "clear_session", fake_clear)
        monkeypatch.setattr(agents_router, "send_to_agent", fake_send)

        _run_prompt(topic_id, f"/agent {agent_uuid} /clear poll the inbox")

        assert calls["clear"] == (agent_id, True)
        assert calls["send"] == "poll the inbox"
        receipts = [m["content"] for m in _messages(client, topic_id)]
        assert any("fresh context" in c for c in receipts)
        asyncio.run(_delete_agent(agent_id))


def test_agent_clear_directive_without_prompt_just_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/agent <uuid> /clear` with no trailing prompt only resets the context."""
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        agent_uuid = "22222222-2222-2222-2222-222222222222"
        agent_id = asyncio.run(_seed_agent(agent_uuid, title="Inbox watcher"))

        cleared: dict[str, object] = {}

        async def fake_clear(self, aid, *, keep_id=False):  # type: ignore[no-untyped-def]
            cleared["call"] = (aid, keep_id)

        async def fail_send(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a bare /clear must not send a follow-up turn")

        from precursor.backend.routers import agents as agents_router
        from precursor.backend.services.agents import manager as mgr_mod

        monkeypatch.setattr(mgr_mod.AgentManager, "clear_session", fake_clear)
        monkeypatch.setattr(agents_router, "send_to_agent", fail_send)

        _run_prompt(topic_id, f"/agent {agent_uuid} /clear")

        assert cleared["call"] == (agent_id, True)
        receipts = [m["content"] for m in _messages(client, topic_id)]
        assert any("Cleared the context" in c for c in receipts)
        asyncio.run(_delete_agent(agent_id))


def test_agent_run_directive_replays_task_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/agent <uuid> /run <extra>` resets context and replays the agent's stored
    task_prompt (instructions live once on the agent, not in the schedule)."""
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        agent_uuid = "33333333-3333-3333-3333-333333333333"
        agent_id = asyncio.run(_seed_agent(agent_uuid, title="Inbox watcher"))

        calls: dict[str, object] = {}

        async def fake_rerun(self, aid, *, extra=None):  # type: ignore[no-untyped-def]
            calls["rerun"] = (aid, extra)

        async def noop_runtime(session):  # type: ignore[no-untyped-def]
            return None

        from precursor.backend.routers import agents as agents_router
        from precursor.backend.services.agents import manager as mgr_mod

        monkeypatch.setattr(mgr_mod.AgentManager, "rerun_task", fake_rerun)
        monkeypatch.setattr(agents_router, "_require_runtime", noop_runtime)

        _run_prompt(topic_id, f"/agent {agent_uuid} /run prioritise FR mail")

        assert calls["rerun"] == (agent_id, "prioritise FR mail")
        receipts = [m["content"] for m in _messages(client, topic_id)]
        assert any("Re-ran agent" in c and "extra note" in c for c in receipts)
        asyncio.run(_delete_agent(agent_id))


def test_command_clear_context_wipes_before_acting(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        asyncio.run(_seed_message(topic_id, "history"))

        async def fail_turn(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a command must not invoke the LLM turn")

        monkeypatch.setattr(sc, "run_topic_turn", fail_turn)
        _run_prompt(topic_id, "/pin", clear_context=True)

        assert all(m["content"] != "history" for m in _messages(client, topic_id))


# ---------------------------------------------------------------------------
# Skills expand like the composer does
# ---------------------------------------------------------------------------


def test_skill_command_expands_to_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        created = client.post(
            "/api/skills",
            json={"name": "summarise", "instructions": "Summarise tersely."},
        )
        assert created.status_code in (200, 201)

        captured: dict[str, object] = {}

        async def fake_turn(tid, prompt, *, clear_context=False, llm_prompt=None):  # type: ignore[no-untyped-def]
            captured["prompt"] = prompt
            captured["llm_prompt"] = llm_prompt

        monkeypatch.setattr(sc, "run_topic_turn", fake_turn)
        _run_prompt(topic_id, "/summarise the meeting")

        # Persisted user turn stays the literal command; LLM sees the expansion.
        assert captured["prompt"] == "/summarise the meeting"
        assert isinstance(captured["llm_prompt"], str)
        assert "Summarise tersely." in captured["llm_prompt"]
        assert captured["llm_prompt"].endswith("the meeting")


async def _seed_message(topic_id: int, content: str) -> None:
    from precursor.backend.models import MessageRole

    async with SessionLocal() as session:
        session.add(Message(topic_id=topic_id, role=MessageRole.USER, content=content))
        await session.commit()


async def _seed_agent(copilot_session_id: str, *, title: str = "Agent") -> int:
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = AgentSession(
            title=title,
            task_prompt="seed",
            status="idle",
            copilot_session_id=copilot_session_id,
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent.id


async def _delete_agent(agent_id: int) -> None:
    from precursor.backend.models import AgentSession

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        if agent is not None:
            await session.delete(agent)
            await session.commit()
