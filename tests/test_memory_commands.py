"""Tests for the memory slash commands + MCP write tools.

Covers the shared argument parsers, the service create/update helpers, the
``build_memory_prompt`` injection, the gated ``store_memory`` / ``update_memory``
MCP tools, and headless scheduled-command dispatch.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting
from precursor.backend.services import memories as memory_service
from precursor.backend.services import scheduled_commands as sc
from precursor.backend.services.mcp import precursor_server as ps

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_parse_store_arg_default_kind() -> None:
    payload = memory_service.parse_store_arg("I prefer concise answers")
    assert payload.kind == "context"
    assert payload.content == "I prefer concise answers"


def test_parse_store_arg_bracket_kind() -> None:
    payload = memory_service.parse_store_arg("[Preference] dark mode please")
    assert payload.kind == "preference"  # normalised to lowercase by the schema
    assert payload.content == "dark mode please"


def test_parse_store_arg_rejects_empty() -> None:
    with pytest.raises(ValueError):
        memory_service.parse_store_arg("   ")
    with pytest.raises(ValueError):
        memory_service.parse_store_arg("[preference]   ")


def test_parse_update_arg_id_and_content() -> None:
    memory_id, payload = memory_service.parse_update_arg("7 new content")
    assert memory_id == 7
    assert payload.content == "new content"
    assert payload.kind is None


def test_parse_update_arg_with_kind() -> None:
    memory_id, payload = memory_service.parse_update_arg("3 [fact] updated text")
    assert memory_id == 3
    assert payload.kind == "fact"
    assert payload.content == "updated text"


def test_parse_update_arg_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        memory_service.parse_update_arg("")
    with pytest.raises(ValueError):
        memory_service.parse_update_arg("notanid content")
    with pytest.raises(ValueError):
        memory_service.parse_update_arg("5")  # id but nothing to change


# ---------------------------------------------------------------------------
# Service helpers + prompt injection
# ---------------------------------------------------------------------------


def test_create_update_and_build_prompt() -> None:
    async def scenario() -> None:
        async with SessionLocal() as session:
            created = await memory_service.create_memory(
                session, memory_service.parse_store_arg("[fact] sky is blue")
            )
            assert created.id is not None

            _id, patch = memory_service.parse_update_arg(f"{created.id} sky is grey")
            updated = await memory_service.update_memory(session, created.id, patch)
            assert updated.content == "sky is grey"
            assert updated.kind == "fact"  # unchanged

            prompt = await memory_service.build_memory_prompt(session)
            assert prompt is not None
            assert "[FACT] sky is grey" in prompt

    asyncio.run(scenario())


def test_update_missing_raises_lookup() -> None:
    async def scenario() -> None:
        async with SessionLocal() as session:
            _id, patch = memory_service.parse_update_arg("999999 nope")
            with pytest.raises(LookupError):
                await memory_service.update_memory(session, 999999, patch)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# MCP write tools (gated by the memory_write section)
# ---------------------------------------------------------------------------


async def _set_expose(value_json: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_expose")
        if row is None:
            session.add(AppSetting(key="mcp_expose", value=value_json))
        else:
            row.value = value_json
        await session.commit()


@pytest.fixture(autouse=True)
def _reset_mcp_expose():
    """Restore the shared ``mcp_expose`` row after each test.

    The test DB is process-wide (see conftest), so the section toggles these
    tests flip must be cleared or they leak into other files' assertions
    (e.g. ``test_mcp_expose_defaults_all_off``).
    """
    yield
    asyncio.run(_set_expose("{}"))


async def test_store_memory_gated_when_section_off() -> None:
    await _set_expose("{}")
    result = await ps.store_memory("blocked content")
    assert "error" in result
    assert "not exposed" in result["error"]


async def test_store_and_update_memory_when_enabled() -> None:
    await _set_expose('{"memory_write": true}')
    stored = await ps.store_memory("remember the milk", kind="todo")
    assert "error" not in stored
    assert stored["kind"] == "todo"
    memory_id = stored["id"]

    updated = await ps.update_memory(memory_id, content="remember oat milk")
    assert updated["content"] == "remember oat milk"
    assert updated["kind"] == "todo"


async def test_update_memory_unknown_id() -> None:
    await _set_expose('{"memory_write": true}')
    result = await ps.update_memory(987654, content="ghost")
    assert "error" in result
    assert "not found" in result["error"]


def test_memory_write_section_exposed_in_settings() -> None:
    app = create_app()
    with TestClient(app) as client:
        expose = client.get("/api/settings").json()["mcp_expose"]
        assert "memory_write" in expose


# ---------------------------------------------------------------------------
# Scheduled-command dispatch
# ---------------------------------------------------------------------------


def _make_topic(client: TestClient) -> int:
    created = client.post("/api/topics", json={"title": "Memory schedule"})
    assert created.status_code in (200, 201)
    return created.json()["id"]


def test_scheduled_memory_store(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)

        async def fail_turn(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a command must not invoke the LLM turn")

        monkeypatch.setattr(sc, "run_topic_turn", fail_turn)
        asyncio.run(sc.run_scheduled_prompt(topic_id, "/memory-store [fact] scheduled note"))

        receipts = [m["content"] for m in client.get(f"/api/topics/{topic_id}/messages").json()]
        assert any("Saved memory" in r for r in receipts)

        memories = client.get("/api/memories").json()
        assert any(m["content"] == "scheduled note" and m["kind"] == "fact" for m in memories)


def test_scheduled_memory_list(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    with TestClient(app) as client:
        topic_id = _make_topic(client)
        client.post("/api/memories", json={"kind": "fact", "content": "listed note"})

        async def fail_turn(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a command must not invoke the LLM turn")

        monkeypatch.setattr(sc, "run_topic_turn", fail_turn)
        asyncio.run(sc.run_scheduled_prompt(topic_id, "/memory-list"))

        receipts = [m["content"] for m in client.get(f"/api/topics/{topic_id}/messages").json()]
        assert any("listed note" in r and "#" in r for r in receipts)
