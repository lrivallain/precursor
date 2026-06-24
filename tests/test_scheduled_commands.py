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
