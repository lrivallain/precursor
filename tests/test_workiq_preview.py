"""WorkIQ preview toggle — transport switch + status surface.

Preview mode flips the built-in ``workiq`` server from the local stdio launcher
to the hosted OAuth HTTP endpoint (writes). These cover the HTTP seam that's
independent of an actual sign-in: toggling is reflected in the catalog and is
reversible. The interactive OAuth flow itself only runs on connect, which we
never trigger here (WorkIQ stays disabled).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services.mcp.workiq_preview import WORKIQ_PREVIEW_URL


def _workiq(servers: list[dict]) -> dict:
    return next(s for s in servers if s["name"] == "workiq")


def test_preview_field_exposed_per_server() -> None:
    app = create_app()
    with TestClient(app) as client:
        servers = client.get("/api/mcp/servers").json()
        # workiq carries the preview flag (off by default); others opt out (None).
        assert _workiq(servers)["preview"] is False
        github = next(s for s in servers if s["name"] == "github")
        assert github["preview"] is None


def test_toggle_preview_switches_transport_and_back() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Default: local stdio launcher.
        wq = _workiq(client.get("/api/mcp/servers").json())
        assert wq["transport"] == "stdio"
        assert wq["preview"] is False

        # Enable preview → hosted HTTP endpoint, still disabled (no sign-in).
        on = client.post("/api/mcp/servers/workiq/preview", json={"enabled": True})
        assert on.status_code == 200
        body = on.json()
        assert body["preview"] is True
        assert body["transport"] == "streamable_http"
        assert body["url"] == WORKIQ_PREVIEW_URL
        assert body["enabled"] is False

        # Persisted in the catalog listing too.
        assert _workiq(client.get("/api/mcp/servers").json())["transport"] == "streamable_http"

        # Disable preview → back to the stdio launcher.
        off = client.post("/api/mcp/servers/workiq/preview", json={"enabled": False})
        assert off.status_code == 200
        assert off.json()["preview"] is False
        assert off.json()["transport"] == "stdio"
