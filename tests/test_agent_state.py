"""Agent state tests — the private cross-run scratchpad.

Covers the seams that make state a *distinct* surface from artifacts and
memories: upsert semantics, the key cap, the keys-only index (bodies never leak
into the prompt), cascade cleanup with the agent, and the MCP tools' implicit
"self" agent resolution.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AgentSession
from precursor.backend.models.agent_state import AGENT_STATE_MAX_KEYS
from precursor.backend.schemas.agent_state import AgentStateWrite
from precursor.backend.services import agent_state as state_service

_created: list[int] = []


@pytest.fixture(autouse=True)
async def _cleanup_agents():
    """Drop the agents a test created.

    The suite shares one SQLite file, and other modules assert on an *empty*
    agent list (``test_agents_disabled_by_default``), so rows left behind here
    would fail them.
    """
    yield
    async with SessionLocal() as session:
        for agent_id in _created:
            agent = await session.get(AgentSession, agent_id)
            if agent is not None:
                await session.delete(agent)
        await session.commit()
    _created.clear()


async def _make_agent(title: str = "state agent") -> int:
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass
    async with SessionLocal() as session:
        agent = AgentSession(title=title, task_prompt="do a thing")
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        _created.append(agent.id)
        return agent.id


async def test_set_state_upserts_by_key() -> None:
    agent_id = await _make_agent()
    async with SessionLocal() as session:
        first, created = await state_service.set_state(
            session, agent_id, AgentStateWrite(key="cursor", value='{"last_id": 1}')
        )
        assert created is True

        second, created_again = await state_service.set_state(
            session, agent_id, AgentStateWrite(key="cursor", value='{"last_id": 42}')
        )
        # Same row updated in place — a re-run must not accumulate duplicates.
        assert created_again is False
        assert second.id == first.id
        assert second.value == '{"last_id": 42}'

        rows = await state_service.list_states(session, agent_id)
        assert len(rows) == 1


async def test_state_survives_and_is_scoped_per_agent() -> None:
    a, b = await _make_agent("a"), await _make_agent("b")
    async with SessionLocal() as session:
        await state_service.set_state(session, a, AgentStateWrite(key="seen", value="a-value"))
        await state_service.set_state(session, b, AgentStateWrite(key="seen", value="b-value"))

        got_a = await state_service.get_state(session, a, "seen")
        got_b = await state_service.get_state(session, b, "seen")
        assert got_a is not None and got_a.value == "a-value"
        assert got_b is not None and got_b.value == "b-value"


async def test_key_index_omits_bodies() -> None:
    agent_id = await _make_agent()
    body = "x" * 5_000
    async with SessionLocal() as session:
        await state_service.set_state(session, agent_id, AgentStateWrite(key="big", value=body))
        entries = await state_service.list_state_keys(session, agent_id)
        assert [(e.key, e.size) for e in entries] == [("big", 5_000)]

        prompt = await state_service.build_state_index_prompt(session, agent_id)
        assert prompt is not None
        assert "`big`" in prompt
        # The whole point: the body stays in the DB until state_get asks for it.
        assert body not in prompt


async def test_key_cap_rejects_new_keys_but_allows_overwrite() -> None:
    agent_id = await _make_agent()
    async with SessionLocal() as session:
        for i in range(AGENT_STATE_MAX_KEYS):
            await state_service.set_state(
                session, agent_id, AgentStateWrite(key=f"k{i}", value="v")
            )

        with pytest.raises(ValueError, match="limited to"):
            await state_service.set_state(
                session, agent_id, AgentStateWrite(key="one-too-many", value="v")
            )

        # An agent at the cap can still make progress on keys it already owns.
        row, created = await state_service.set_state(
            session, agent_id, AgentStateWrite(key="k0", value="updated")
        )
        assert created is False and row.value == "updated"


async def test_state_is_deleted_with_its_agent() -> None:
    agent_id = await _make_agent()
    async with SessionLocal() as session:
        await state_service.set_state(session, agent_id, AgentStateWrite(key="cursor", value="1"))

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        await session.delete(agent)
        await session.commit()

    async with SessionLocal() as session:
        # SQLite runs with foreign keys off, so this proves the ORM cascade (not
        # the DB constraint) does the cleanup.
        remaining = await state_service.list_states(session, agent_id)
        assert remaining == []


def test_invalid_keys_are_rejected() -> None:
    for bad in ("Has Space", "-leading", "sym$bol", ""):
        with pytest.raises(ValueError):
            AgentStateWrite(key=bad, value="v")
    # Case is normalised rather than rejected (same as memory kinds), and the
    # namespacing separators are the point of the format.
    assert AgentStateWrite(key="UPPER", value="v").key == "upper"
    assert AgentStateWrite(key="inbox.last_seen-id", value="v").key == "inbox.last_seen-id"


async def test_http_state_roundtrip() -> None:
    agent_id = await _make_agent()
    app = create_app()
    with TestClient(app) as client:
        assert client.get(f"/api/agents/{agent_id}/state").json() == []

        written = client.put(f"/api/agents/{agent_id}/state", json={"key": "cursor", "value": "42"})
        assert written.status_code == 200
        assert written.json()["key"] == "cursor"

        listed = client.get(f"/api/agents/{agent_id}/state").json()
        assert [r["key"] for r in listed] == ["cursor"]

        assert client.delete(f"/api/agents/{agent_id}/state/cursor").status_code == 204
        assert client.delete(f"/api/agents/{agent_id}/state/cursor").status_code == 404


async def test_http_clear_wipes_scratchpad() -> None:
    agent_id = await _make_agent()
    app = create_app()
    with TestClient(app) as client:
        client.put(f"/api/agents/{agent_id}/state", json={"key": "a", "value": "1"})
        client.put(f"/api/agents/{agent_id}/state", json={"key": "b", "value": "2"})
        assert client.delete(f"/api/agents/{agent_id}/state").status_code == 204
        assert client.get(f"/api/agents/{agent_id}/state").json() == []


async def test_mcp_tools_default_to_the_calling_agent(monkeypatch) -> None:
    """``PRECURSOR_AGENT_ID`` lets an agent address its own scratchpad."""
    from precursor.backend.services.mcp import precursor_server as ps

    agent_id = await _make_agent()
    monkeypatch.setenv("PRECURSOR_MCP_FULL_ACCESS", "1")
    monkeypatch.setenv("PRECURSOR_AGENT_ID", str(agent_id))

    saved = await ps.state_set(key="cursor", value='{"page": 3}')
    assert saved == {"agent_id": agent_id, "key": "cursor", "created": True, "size": 11}

    read = await ps.state_get(key="cursor")
    assert read["found"] is True and read["value"] == '{"page": 3}'

    listed = await ps.state_list()
    assert [k["key"] for k in listed["keys"]] == ["cursor"]
    assert "value" not in listed["keys"][0]

    missing = await ps.state_get(key="never-written")
    assert missing["found"] is False and "error" not in missing

    assert (await ps.state_delete(key="cursor"))["deleted"] is True


async def test_mcp_state_requires_an_agent_without_context(monkeypatch) -> None:
    from precursor.backend.services.mcp import precursor_server as ps

    monkeypatch.setenv("PRECURSOR_MCP_FULL_ACCESS", "1")
    monkeypatch.delenv("PRECURSOR_AGENT_ID", raising=False)

    result = await ps.state_list()
    assert "No agent in context" in result["error"]


async def test_mcp_state_is_gated_for_external_clients(monkeypatch) -> None:
    from precursor.backend.services.mcp import precursor_server as ps

    monkeypatch.delenv("PRECURSOR_MCP_FULL_ACCESS", raising=False)
    agent_id = await _make_agent()

    result = await ps.state_list(agent_id=agent_id)
    assert "not exposed" in result["error"]
