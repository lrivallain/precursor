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
