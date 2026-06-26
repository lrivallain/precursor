"""Tests for the cmd-runner MCP server registration + Docker jail preflight."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import cmd_runner


def test_preflight_skipped_when_jail_disabled() -> None:
    assert cmd_runner.jail_preflight_error(False) is None


def test_preflight_blocks_when_docker_missing(monkeypatch) -> None:
    monkeypatch.setattr(cmd_runner, "docker_available", lambda: (False, "no docker"))
    msg = cmd_runner.jail_preflight_error(True)
    assert msg is not None
    assert "Docker" in msg


def test_preflight_ok_when_docker_present(monkeypatch) -> None:
    monkeypatch.setattr(cmd_runner, "docker_available", lambda: (True, "27.0"))
    assert cmd_runner.jail_preflight_error(True) is None


def test_cmd_runner_registered_as_builtin() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        servers = r.json()
        entry = next((s for s in servers if s["name"] == "cmd-runner"), None)
        assert entry is not None
        assert entry["builtin"] is True


def test_connect_refuses_when_docker_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(cmd_runner, "docker_available", lambda: (False, "no docker"))
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/mcp/servers/cmd-runner/connect")
        assert r.status_code == 200
        body = r.json()
        # Refused: stays disabled, with the known reason surfaced in `error`.
        assert body["enabled"] is False
        assert "Docker" in (body["error"] or "")


def test_system_settings_round_trip() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Defaults are surfaced in the settings read.
        r = client.get("/api/settings")
        assert r.status_code == 200
        before = r.json()
        assert "cmd_runner_jail" in before
        assert "llm_max_input_tokens" in before

        # Override via PUT and confirm it round-trips.
        r = client.put(
            "/api/settings",
            json={
                "cmd_runner_jail": False,
                "cmd_runner_image": "node:22-slim",
                "llm_max_input_tokens": 123_456,
                "scheduled_run_timeout_seconds": 42,
            },
        )
        assert r.status_code == 200
        after = r.json()
        assert after["cmd_runner_jail"] is False
        assert after["cmd_runner_image"] == "node:22-slim"
        assert after["llm_max_input_tokens"] == 123_456
        assert after["scheduled_run_timeout_seconds"] == 42


def test_connect_allowed_when_jail_disabled_in_db() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Disable jail via settings — cmd-runner no longer needs Docker.
        client.put("/api/settings", json={"cmd_runner_jail": False})
        r = client.post("/api/mcp/servers/cmd-runner/connect")
        assert r.status_code == 200
        assert r.json()["enabled"] is True


def test_refresh_unknown_server_returns_404() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/mcp/servers/does-not-exist/refresh")
        assert r.status_code == 404


def test_refresh_disabled_server_returns_409() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Ensure cmd-runner is disabled regardless of earlier tests' state.
        client.post("/api/mcp/servers/cmd-runner/disconnect")
        r = client.post("/api/mcp/servers/cmd-runner/refresh")
        assert r.status_code == 409


def test_refresh_enabled_server_succeeds() -> None:
    app = create_app()
    with TestClient(app) as client:
        client.put("/api/settings", json={"cmd_runner_jail": False})
        assert client.post("/api/mcp/servers/cmd-runner/connect").status_code == 200
        r = client.post("/api/mcp/servers/cmd-runner/refresh")
        assert r.status_code == 200
        assert r.json()["enabled"] is True
