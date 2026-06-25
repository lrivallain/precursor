"""WorkIQ preview toggle — transport switch + status surface.

Preview mode flips the built-in ``workiq`` server from the local stdio launcher
to the hosted OAuth HTTP endpoint (writes). These cover the HTTP seam that's
independent of an actual sign-in: toggling is reflected in the catalog and is
reversible. The interactive OAuth flow itself only runs on connect, which we
never trigger here (WorkIQ stays disabled).
"""

from __future__ import annotations

import pytest
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


async def test_background_redirect_handler_refuses_browser(monkeypatch) -> None:
    """A non-interactive provider must never pop a browser; it signals re-auth."""
    from precursor.backend.services.mcp import workiq_preview as wp

    opened: list[str] = []
    monkeypatch.setattr(wp.webbrowser, "open", lambda url: opened.append(url))

    background = wp._make_redirect_handler(interactive=False)
    with pytest.raises(wp.WorkIQAuthRequiredError):
        await background("https://login.example/authorize?x=1")
    assert opened == []  # browser stayed shut

    interactive = wp._make_redirect_handler(interactive=True)
    await interactive("https://login.example/authorize?x=1")
    assert opened == ["https://login.example/authorize?x=1"]


async def test_reauthenticate_single_flight() -> None:
    """A second sign-in while one is running is rejected, not queued."""
    from precursor.backend.services.mcp import workiq_preview as wp

    async with wp._reauth_lock:
        with pytest.raises(wp.WorkIQAuthInProgressError):
            await wp.reauthenticate_workiq()


def test_reauthenticate_requires_preview_enabled() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Preview off by default → the endpoint refuses rather than opening a flow.
        resp = client.post("/api/mcp/servers/workiq/reauthenticate")
        assert resp.status_code == 400


def test_reauthenticate_runs_flow_and_reports_status(monkeypatch) -> None:
    from precursor.backend.services.mcp import workiq_preview as wp

    ran: list[bool] = []

    async def _fake_flow() -> None:
        ran.append(True)

    monkeypatch.setattr(wp, "reauthenticate_workiq", _fake_flow)

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/mcp/servers/workiq/preview", json={"enabled": True})
        resp = client.post("/api/mcp/servers/workiq/reauthenticate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["preview"] is True
        assert body["transport"] == "streamable_http"
        assert ran == [True]
