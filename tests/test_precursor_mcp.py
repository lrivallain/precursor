"""Tests for the built-in 'precursor' outbound MCP server + mcp_expose gating."""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.services.app_settings import (
    MCP_EXPOSE_SECTIONS,
    resolve_mcp_expose,
)
from precursor.backend.services.mcp import precursor_server as ps


def test_precursor_registered_as_builtin() -> None:
    app = create_app()
    with TestClient(app) as client:
        servers = client.get("/api/mcp/servers").json()
        entry = next((s for s in servers if s["name"] == "precursor"), None)
        assert entry is not None
        assert entry["builtin"] is True


def test_server_info_describes_sections() -> None:
    app = create_app()
    with TestClient(app) as client:
        info = client.get("/api/mcp/server/info").json()
        assert info["name"] == "precursor"
        assert info["transport"] == "stdio"
        assert set(info["sections"]) == set(MCP_EXPOSE_SECTIONS)
        tool_names = {t["name"] for t in info["tools"]}
        assert {"list_topics", "post_message", "create_schedule"} <= tool_names


def test_mcp_expose_defaults_all_off() -> None:
    app = create_app()
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        expose = settings["mcp_expose"]
        assert set(expose) == set(MCP_EXPOSE_SECTIONS)
        assert all(v is False for v in expose.values())


def test_mcp_expose_round_trip() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.put(
            "/api/settings",
            json={"mcp_expose": {"topics": True, "post_message": True}},
        )
        assert r.status_code == 200
        expose = r.json()["mcp_expose"]
        assert expose["topics"] is True
        assert expose["post_message"] is True
        assert expose["schedules"] is False


async def _set_expose(value_json: str) -> None:
    """Upsert the mcp_expose AppSetting row directly (tests share one DB)."""
    from precursor.backend.models import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_expose")
        if row is None:
            session.add(AppSetting(key="mcp_expose", value=value_json))
        else:
            row.value = value_json
        await session.commit()


async def test_tool_gated_when_section_off() -> None:
    # Explicit all-off so the test is independent of other tests' writes.
    await _set_expose("{}")
    result = await ps.list_topics()
    assert "error" in result
    assert "not exposed" in result["error"]


async def test_tool_runs_when_section_enabled() -> None:
    await _set_expose('{"topics": true}')
    async with SessionLocal() as session:
        expose = await resolve_mcp_expose(session)
        assert expose["topics"] is True

    result = await ps.list_topics()
    assert "error" not in result
    assert "topics" in result


def _mcp_post(client: TestClient, body: dict, session_id: str | None = None) -> object:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return client.post("/mcp", json=body, headers=headers)


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


def test_http_transport_404_when_disabled() -> None:
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        r = _mcp_post(client, _INIT)
        assert r.status_code == 404


def test_http_transport_handshake_when_enabled() -> None:
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.put("/api/settings", json={"mcp_http_enabled": True})
        r = _mcp_post(client, _INIT)
        assert r.status_code == 200
        sid = r.headers.get("mcp-session-id")
        assert sid
        _mcp_post(
            client,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=sid,
        )
        r2 = _mcp_post(
            client,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id=sid,
        )
        assert r2.status_code == 200
        assert "list_topics" in r2.text
        assert "post_message" in r2.text


def test_http_transport_rejects_foreign_host() -> None:
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.put("/api/settings", json={"mcp_http_enabled": True})
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Host": "evil.example.com",
        }
        r = client.post("/mcp", json=_INIT, headers=headers)
        # FastMCP's Host allowlist rejects non-localhost Host headers.
        assert r.status_code == 421


def test_http_transport_bare_path_not_405() -> None:
    # Regression: the bare /mcp URL (no trailing slash) must reach the MCP
    # handler, not get shadowed by the SPA catch-all (which produced 405).
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.put("/api/settings", json={"mcp_http_enabled": True})
        r = client.post(
            "/mcp",
            json=_INIT,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            follow_redirects=False,
        )
        assert r.status_code == 200


def test_settings_expose_http_fields() -> None:
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        # Reset to the default so this test is independent of other tests' writes.
        client.put("/api/settings", json={"mcp_http_enabled": False})
        s = client.get("/api/settings").json()
        assert s["mcp_http_enabled"] is False
        assert s["mcp_http_loopback_ok"] is True
        assert s["mcp_http_url"] == "http://127.0.0.1:8000/mcp"
