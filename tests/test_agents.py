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


async def _add_mcp_server(**fields) -> None:
    from precursor.backend.db import SessionLocal
    from precursor.backend.models import MCPServer
    from precursor.backend.services.mcp.user_servers import get_row_by_name

    async with SessionLocal() as session:
        existing = await get_row_by_name(session, fields["name"])
        if existing is not None:
            await session.delete(existing)
            await session.commit()
        session.add(MCPServer(**fields))
        await session.commit()


async def test_user_mcp_configs_translate_rows_to_sdk() -> None:
    """User MCP rows become SDK configs an agent session can attach."""
    import pytest

    from precursor.backend.services.agents import runtime
    from precursor.backend.services.agents.manager import AgentManager

    if not runtime.sdk_installed():
        pytest.skip("github-copilot-sdk not installed")

    # Initialise the schema (alembic upgrade runs on app startup).
    with TestClient(create_app()):
        pass

    await _add_mcp_server(
        name="my-stdio",
        transport="stdio",
        command="my-cmd",
        args_json='["--flag", "value"]',
        headers_json="{}",
    )
    await _add_mcp_server(
        name="my-http",
        transport="streamable_http",
        url="https://example.test/mcp",
        args_json="[]",
        headers_json='{"Authorization": "Bearer secret-token"}',
    )
    # A reserved name (must never shadow the built-in) and an unrepresentable
    # transport (must be skipped, not raise).
    await _add_mcp_server(
        name="precursor",
        transport="stdio",
        command="evil",
        args_json="[]",
        headers_json="{}",
    )
    await _add_mcp_server(
        name="bogus",
        transport="websocket",
        url="wss://example.test",
        args_json="[]",
        headers_json="{}",
    )

    configs = await AgentManager()._user_mcp_configs()

    assert "bogus" not in configs  # unsupported transport skipped
    stdio = configs["my-stdio"]
    assert stdio["type"] == "stdio"
    assert stdio["command"] == "my-cmd"
    assert stdio["args"] == ["--flag", "value"]
    assert stdio["tools"] == ["*"]

    http = configs["my-http"]
    assert http["type"] == "http"
    assert http["url"] == "https://example.test/mcp"
    assert http["headers"] == {"Authorization": "Bearer secret-token"}
    assert http["tools"] == ["*"]

    # A user row named 'precursor' is present here, but merging in _ensure_live
    # drops it; verify the helper at least surfaces it so the merge can reject it.
    assert "precursor" in configs

    # Clean up so the rows don't leak into other tests sharing the DB.
    for name in ("my-stdio", "my-http", "precursor", "bogus"):
        from precursor.backend.db import SessionLocal
        from precursor.backend.services.mcp.user_servers import get_row_by_name

        async with SessionLocal() as session:
            row = await get_row_by_name(session, name)
            if row is not None:
                await session.delete(row)
                await session.commit()
