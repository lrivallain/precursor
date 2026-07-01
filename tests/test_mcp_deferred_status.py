"""Tests for deferred MCP status loading.

The Settings panel loads the server *list* first (``GET /servers?probe=false``)
so a slow server never stalls the whole listing, then resolves each server's
status independently via ``POST /servers/{name}/probe``.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting
from precursor.backend.services.mcp.client import get_mcp_client_manager


async def _set_enabled(name: str, value: bool) -> None:
    """Upsert the mcp_enabled AppSetting row directly (tests share one DB)."""
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "mcp_enabled")
        data: dict[str, bool] = json.loads(row.value) if row else {}
        data[name] = value
        encoded = json.dumps(data)
        if row is None:
            session.add(AppSetting(key="mcp_enabled", value=encoded))
        else:
            row.value = encoded
        await session.commit()


def _find(servers: list[dict], name: str) -> dict | None:
    return next((s for s in servers if s["name"] == name), None)


def test_list_without_probe_marks_stale_enabled_as_connecting() -> None:
    """An enabled server with no cached tools reports ``connecting`` so the UI
    can render its card immediately and probe it afterwards."""
    app = create_app()
    with TestClient(app) as client:
        # Simulate an enabled server whose tool catalogue hasn't been probed yet
        # (e.g. right after a process restart).
        manager = get_mcp_client_manager()
        entry = manager.get("precursor")
        assert entry is not None
        entry.tools = []
        entry.state = "disconnected"
        asyncio.run(_set_enabled("precursor", True))

        deferred = _find(client.get("/api/mcp/servers?probe=false").json(), "precursor")
        assert deferred is not None
        assert deferred["state"] == "connecting"


def test_probe_endpoint_unknown_server_returns_404() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/mcp/servers/does-not-exist/probe")
        assert r.status_code == 404


def test_probe_endpoint_disabled_server_stays_disabled() -> None:
    """Probing a disabled server just reports its status without connecting."""
    app = create_app()
    with TestClient(app) as client:
        asyncio.run(_set_enabled("precursor", False))
        r = client.post("/api/mcp/servers/precursor/probe")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "precursor"
        assert body["enabled"] is False
        assert body["state"] == "disabled"
