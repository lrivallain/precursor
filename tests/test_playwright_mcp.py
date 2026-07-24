"""Tests for the playwright MCP server registration + npx preflight."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.mcp import client as mcp_client


def test_preflight_ok_when_npx_present(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (True, "/usr/bin/npx"))
    assert mcp_client.playwright_preflight_error() is None


def test_preflight_blocks_when_npx_missing(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (False, "npx not found"))
    msg = mcp_client.playwright_preflight_error()
    assert msg is not None
    assert "Node.js" in msg


def test_playwright_registered_as_builtin() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        entry = next((s for s in r.json() if s["name"] == "playwright"), None)
        assert entry is not None
        assert entry["builtin"] is True


def test_playwright_uses_persistent_profile() -> None:
    entry = mcp_client.get_mcp_client_manager().get("playwright")
    assert entry is not None
    assert entry.command == "npx"
    # A persistent --user-data-dir is what keeps the authenticated session alive
    # across runs, so it must be wired into the launch args.
    assert "--user-data-dir" in entry.args


def test_connect_refuses_when_npx_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(mcp_client, "npx_available", lambda: (False, "npx not found"))
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/mcp/servers/playwright/connect")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert "Node.js" in (body["error"] or "")
