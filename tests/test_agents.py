"""Agents API tests — the non-SDK seams of Agents mode.

The live Copilot SDK runtime can't run here (no subscription / binary in CI), so
these tests cover the HTTP surface that's independent of it: the feature is
opt-in and off by default, so listing is empty and creating a task is refused
until the operator enables it. The settings endpoint advertises the gate.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app


def test_agents_disabled_by_default() -> None:
    app = create_app()
    with TestClient(app) as client:
        listed = client.get("/api/agents")
        assert listed.status_code == 200
        assert listed.json() == []

        created = client.post("/api/agents", json={"task": "do a thing"})
        assert created.status_code == 409
        assert "disabled" in created.json()["detail"].lower()


def test_settings_expose_agents_gate() -> None:
    app = create_app()
    with TestClient(app) as client:
        body = client.get("/api/settings").json()
        assert body["agents_enabled"] is False
        # availability is a runtime probe — only the key/type contract matters.
        assert isinstance(body["agents_available"], bool)
        assert isinstance(body["agents_default_model"], str)


def test_enabling_agents_persists_and_is_reported(monkeypatch) -> None:
    # Neutralise the runtime probe so flipping the toggle doesn't try to launch a
    # real Copilot CLI process during the test (manager.start gates on this).
    from precursor.backend.services.agents import runtime

    monkeypatch.setattr(runtime, "agents_available", lambda: (False, "test: disabled"))

    app = create_app()
    with TestClient(app) as client:
        updated = client.put("/api/settings", json={"agents_enabled": True})
        assert updated.status_code == 200
        assert updated.json()["agents_enabled"] is True
        assert client.get("/api/settings").json()["agents_enabled"] is True

        # Reset so the flag doesn't leak into other tests sharing the DB — a later
        # app startup would otherwise try to launch the real Copilot runtime.
        reset = client.put("/api/settings", json={"agents_enabled": False})
        assert reset.json()["agents_enabled"] is False


async def _set_mcp_enabled(mapping: dict[str, bool]) -> None:
    import json

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_enabled")
        encoded = json.dumps(mapping)
        if row is None:
            session.add(AppSetting(key="mcp_enabled", value=encoded))
        else:
            row.value = encoded
        await session.commit()


async def test_catalog_mcp_configs_attaches_enabled_servers() -> None:
    """Agents attach enabled catalog servers (built-in + user), never precursor."""
    import pytest

    from precursor.backend.services.agents import runtime
    from precursor.backend.services.agents.manager import AgentManager
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    if not runtime.sdk_installed():
        pytest.skip("github-copilot-sdk not installed")

    # Initialise the schema (alembic upgrade runs on app startup).
    with TestClient(create_app()):
        pass

    manager = get_mcp_client_manager()
    # A user server with stored headers (exercises the secret-folding path) and
    # a malformed stdio user server (no command → skipped, not raised).
    manager.register_user_entry(
        name="my-http",
        transport="streamable_http",
        url="https://example.test/mcp",
        headers={"Authorization": "Bearer secret-token"},
    )
    manager.register_user_entry(
        name="my-broken",
        transport="stdio",
        command=None,
    )
    try:
        # Enable two built-ins (one stdio, one http), the user http server, and
        # the broken one; leave 'workiq' disabled and 'precursor' enabled.
        await _set_mcp_enabled(
            {
                "fetch": True,
                "github": True,
                "workiq": False,
                "my-http": True,
                "my-broken": True,
                "precursor": True,
            }
        )

        configs = await AgentManager()._catalog_mcp_configs()

        # precursor is attached separately with full access — never here.
        assert "precursor" not in configs
        # Disabled built-in excluded; malformed entry skipped.
        assert "workiq" not in configs
        assert "my-broken" not in configs

        # Built-in stdio server.
        fetch = configs["fetch"]
        assert fetch["type"] == "stdio"
        assert fetch["tools"] == ["*"]

        # Built-in remote server.
        github = configs["github"]
        assert github["type"] == "http"
        assert github["url"] == "https://api.githubcopilot.com/mcp/"

        # User server with its stored Authorization header folded in.
        http = configs["my-http"]
        assert http["type"] == "http"
        assert http["url"] == "https://example.test/mcp"
        assert http["headers"] == {"Authorization": "Bearer secret-token"}
        assert http["tools"] == ["*"]
    finally:
        manager.unregister_user_entry("my-http")
        manager.unregister_user_entry("my-broken")
        await _set_mcp_enabled({})


def test_parse_agent_command() -> None:
    from precursor.backend.services.agents.manager import parse_agent_command

    assert parse_agent_command("hello there") is None
    assert parse_agent_command("  not a / command") is None
    assert parse_agent_command("/rename New Title") == ("rename", "New Title")
    # Leading whitespace tolerated; name lowercased; argument trimmed.
    assert parse_agent_command("  /Rename   New Title  ") == ("rename", "New Title")
    assert parse_agent_command("/clear") == ("clear", "")
    # Unknown commands still parse (so the caller can reject them by name).
    assert parse_agent_command("/whatever do stuff") == ("whatever", "do stuff")


async def _make_agent(**overrides: object) -> int:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession

    fields: dict[str, object] = {"title": "Old title", "task_prompt": "seed", "status": "idle"}
    fields.update(overrides)
    async with SessionLocal() as session:
        agent = AgentSession(**fields)
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent.id


async def test_run_command_rename() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    await AgentManager().run_command(agent_id, "rename", "  Shiny   New   Name ")
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.title == "Shiny New Name"


async def test_run_command_rename_requires_argument() -> None:
    import pytest

    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    with pytest.raises(ValueError, match="Usage: /rename"):
        await AgentManager().run_command(agent_id, "rename", "   ")


async def test_run_command_archive() -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    await AgentManager().run_command(agent_id, "archive", "")
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        assert agent.archived_at is not None


async def test_run_command_clear_resets_session_and_timeline() -> None:
    from sqlalchemy import select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import AgentEventRecord, AgentSession
    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent(
        copilot_session_id="sess-123",
        status="completed",
        active_prompt="in flight",
        result_summary="done",
        error="boom",
    )
    # Seed an archived timeline event that clear should wipe.
    async with SessionLocal() as session:
        session.add(AgentEventRecord(agent_session_id=agent_id, payload='{"kind":"assistant"}'))
        await session.commit()

    await AgentManager().run_command(agent_id, "clear", "")

    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        assert agent is not None
        # A fresh SDK session id is minted (so the next turn starts clean) — never
        # left null, and never the previous handle.
        assert agent.copilot_session_id is not None
        assert agent.copilot_session_id != "sess-123"
        assert agent.status == "idle"
        assert agent.active_prompt is None
        assert agent.result_summary is None
        assert agent.error is None
        remaining = (
            await session.scalars(
                select(AgentEventRecord).where(AgentEventRecord.agent_session_id == agent_id)
            )
        ).all()
        assert remaining == []


async def test_run_command_rejects_unknown() -> None:
    import pytest

    from precursor.backend.services.agents.manager import AgentManager

    with TestClient(create_app()):
        pass
    agent_id = await _make_agent()

    with pytest.raises(ValueError, match="isn't available"):
        await AgentManager().run_command(agent_id, "role", "assistant")


def test_agent_command_registry_is_source_of_truth() -> None:
    """The registry keys drive both validation and the rejection message."""
    from precursor.backend.services.agents.manager import AgentManager

    assert set(AgentManager.supported_commands()) == {"rename", "archive", "clear"}
    assert set(AgentManager._COMMAND_HANDLERS) == set(AgentManager.supported_commands())
