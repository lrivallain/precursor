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


async def test_resolve_bearer_returns_none_on_group_wrapped_auth_required(monkeypatch) -> None:
    """A sign-in requirement wrapped in a TaskGroup must not yield a stale token.

    The SDK's streamable-http transport raises inside an anyio task group, so our
    non-interactive ``WorkIQAuthRequiredError`` surfaces as a
    ``BaseExceptionGroup``. We must unwrap it and return None (skip attaching)
    rather than log a transport failure and hand back the dead stored token.
    """
    import contextlib

    from mcp.shared.auth import OAuthToken

    from precursor.backend.services.mcp import workiq_preview as wp

    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens()
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(access_token="stale", token_type="Bearer", expires_in=3600)
    )

    @contextlib.asynccontextmanager
    async def _raising_client(*_args, **_kwargs):
        raise BaseExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)",
            [wp.WorkIQAuthRequiredError("sign in")],
        )
        yield  # pragma: no cover - never reached

    warnings: list[str] = []
    monkeypatch.setattr(wp, "streamablehttp_client", _raising_client)
    monkeypatch.setattr(wp.logger, "warning", lambda *a, **k: warnings.append(a[0]))

    # Auth-required → None (don't hand the agent the dead token) and no scary warning.
    assert await wp.resolve_workiq_bearer_token() is None
    assert warnings == []


async def test_resolve_bearer_falls_back_on_transient_error(monkeypatch) -> None:
    """A genuine transport blip still falls back to the stored token."""
    import contextlib

    from mcp.shared.auth import OAuthToken

    from precursor.backend.services.mcp import workiq_preview as wp

    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens()
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(access_token="warm", token_type="Bearer", expires_in=3600)
    )

    @contextlib.asynccontextmanager
    async def _raising_client(*_args, **_kwargs):
        raise BaseExceptionGroup("boom", [ConnectionError("network down")])
        yield  # pragma: no cover - never reached

    warnings: list[str] = []
    monkeypatch.setattr(wp, "streamablehttp_client", _raising_client)
    monkeypatch.setattr(wp.logger, "warning", lambda *a, **k: warnings.append(a[0]))

    resolved = await wp.resolve_workiq_bearer_token()
    assert resolved is not None
    assert resolved[0] == "warm"
    assert len(warnings) == 1


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
