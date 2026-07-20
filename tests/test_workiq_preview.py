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
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(wp.webbrowser, "open", lambda url: opened.append(url))

    async def _fake_publish(server: str, url: str) -> None:
        published.append((server, url))

    monkeypatch.setattr(wp, "publish_mcp_auth_url", _fake_publish)

    background = wp._make_redirect_handler(interactive=False)
    with pytest.raises(wp.WorkIQAuthRequiredError):
        await background("https://login.example/authorize?x=1")
    assert opened == []  # browser stayed shut
    assert published == []  # and no URL leaked to the UI

    interactive = wp._make_redirect_handler(interactive=True)
    await interactive("https://login.example/authorize?x=1")
    # Default surfaces the URL to the UI *and* opens the OS browser as a fallback.
    assert opened == ["https://login.example/authorize?x=1"]
    assert published == [("workiq", "https://login.example/authorize?x=1")]


async def test_interactive_handler_skips_browser_when_popup_drives(monkeypatch) -> None:
    """When the SPA drives a popup we surface the URL but skip the OS browser."""
    from precursor.backend.services.mcp import workiq_preview as wp

    opened: list[str] = []
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(wp.webbrowser, "open", lambda url: opened.append(url))

    async def _fake_publish(server: str, url: str) -> None:
        published.append((server, url))

    monkeypatch.setattr(wp, "publish_mcp_auth_url", _fake_publish)

    handler = wp._make_redirect_handler(interactive=True, open_system_browser=False)
    await handler("https://login.example/authorize?x=1")
    assert opened == []  # no stray OS-browser tab
    assert published == [("workiq", "https://login.example/authorize?x=1")]


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


def test_augment_authorization_url_adds_and_preserves() -> None:
    """We splice in login_hint/prompt without clobbering SDK-set params."""
    from precursor.backend.services.mcp import workiq_preview as wp

    base = "https://login.example/authorize?client_id=abc&scope=openid"

    # No-op when nothing to add.
    assert wp._augment_authorization_url(base, login_hint=None, prompt=None) == base

    out = wp._augment_authorization_url(base, login_hint="user@contoso.com", prompt="none")
    from urllib.parse import parse_qs, urlsplit

    params = parse_qs(urlsplit(out).query)
    assert params["login_hint"] == ["user@contoso.com"]
    assert params["prompt"] == ["none"]
    # Original params survive untouched.
    assert params["client_id"] == ["abc"]
    assert params["scope"] == ["openid"]

    # An existing param wins — we never overwrite what the SDK set.
    preset = base + "&prompt=login&login_hint=someone@else.com"
    out2 = wp._augment_authorization_url(preset, login_hint="user@contoso.com", prompt="none")
    p2 = parse_qs(urlsplit(out2).query)
    assert p2["prompt"] == ["login"]
    assert p2["login_hint"] == ["someone@else.com"]


def _fake_jwt(claims: dict) -> str:
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def test_login_hint_from_access_token_decodes_username() -> None:
    from precursor.backend.services.mcp import workiq_preview as wp

    assert (
        wp._login_hint_from_access_token(_fake_jwt({"preferred_username": "u@contoso.com"}))
        == "u@contoso.com"
    )
    # Falls back through the claim priority list.
    assert (
        wp._login_hint_from_access_token(_fake_jwt({"upn": "up@contoso.com"})) == "up@contoso.com"
    )
    # Opaque / malformed tokens yield no hint rather than raising.
    assert wp._login_hint_from_access_token("not-a-jwt") is None
    assert wp._login_hint_from_access_token(_fake_jwt({"sub": "no-username-claim"})) is None


async def test_login_hint_persisted_and_survives_token_clear() -> None:
    """set_tokens captures the account; clearing tokens keeps the hint."""
    from mcp.shared.auth import OAuthToken

    from precursor.backend.services.mcp import workiq_preview as wp

    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens()
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(
            access_token=_fake_jwt({"preferred_username": "hint@contoso.com"}),
            token_type="Bearer",
            expires_in=3600,
            refresh_token="r",
        )
    )
    assert await wp.get_workiq_login_hint() == "hint@contoso.com"

    # Clearing the tokens must NOT drop the hint — it's not a credential, and we
    # reuse it to pre-select the account on the next re-auth.
    await wp.clear_workiq_oauth_tokens()
    assert await wp.get_workiq_login_hint() == "hint@contoso.com"


async def test_try_silent_reauth_falls_back_on_interaction_required(monkeypatch) -> None:
    """A group-wrapped interaction-required signal means 'prompt the user'."""
    from precursor.backend.services.mcp import workiq_preview as wp

    async def _needs_interaction(_provider) -> None:
        raise BaseExceptionGroup("grp", [wp.WorkIQInteractionRequiredError("login_required")])

    monkeypatch.setattr(wp, "_run_signin", _needs_interaction)
    assert (
        await wp._try_silent_reauth(login_hint="u@contoso.com", open_system_browser=False) is False
    )

    async def _ok(_provider) -> None:
        return None

    monkeypatch.setattr(wp, "_run_signin", _ok)
    assert await wp._try_silent_reauth(login_hint=None, open_system_browser=False) is True

    async def _boom(_provider) -> None:
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(wp, "_run_signin", _boom)
    with pytest.raises(RuntimeError, match="transport exploded"):
        await wp._try_silent_reauth(login_hint=None, open_system_browser=False)


async def test_reauthenticate_prefers_silent_then_interactive(monkeypatch) -> None:
    """Silent success skips the prompt; silent fallback runs it; toggle disables it."""
    import types

    from precursor.backend.services.mcp import workiq_preview as wp

    events: list[str] = []

    async def _noop_clear() -> None:
        events.append("clear")

    async def _hint() -> str | None:
        return "u@contoso.com"

    async def _run_signin(_provider) -> None:
        events.append("interactive")

    monkeypatch.setattr(wp, "clear_workiq_oauth_tokens", _noop_clear)
    monkeypatch.setattr(wp, "get_workiq_login_hint", _hint)
    monkeypatch.setattr(wp, "_run_signin", _run_signin)

    def _settings(enabled: bool):
        return lambda: types.SimpleNamespace(workiq_silent_reauth_enabled=enabled)

    # Silent succeeds → no interactive prompt.
    async def _silent_ok(*, login_hint, open_system_browser) -> bool:
        events.append(f"silent({login_hint})")
        return True

    monkeypatch.setattr(wp, "get_settings", _settings(True))
    monkeypatch.setattr(wp, "_try_silent_reauth", _silent_ok)
    events.clear()
    await wp.reauthenticate_workiq(open_system_browser=False)
    assert events == ["clear", "silent(u@contoso.com)"]

    # Silent needs interaction → falls back to the interactive prompt.
    async def _silent_fail(*, login_hint, open_system_browser) -> bool:
        events.append("silent")
        return False

    monkeypatch.setattr(wp, "_try_silent_reauth", _silent_fail)
    events.clear()
    await wp.reauthenticate_workiq(open_system_browser=False)
    assert events == ["clear", "silent", "interactive"]

    # Silent disabled → straight to the interactive prompt, no silent pass.
    async def _silent_unexpected(*, login_hint, open_system_browser) -> bool:  # pragma: no cover
        events.append("silent-should-not-run")
        return True

    monkeypatch.setattr(wp, "get_settings", _settings(False))
    monkeypatch.setattr(wp, "_try_silent_reauth", _silent_unexpected)
    events.clear()
    await wp.reauthenticate_workiq(open_system_browser=False)
    assert events == ["clear", "interactive"]


async def test_reauthenticate_silent_only_never_falls_back(monkeypatch) -> None:
    """The hands-free silent pass returns its outcome and never prompts."""
    from precursor.backend.services.mcp import workiq_preview as wp

    events: list[str] = []

    async def _noop_clear() -> None:
        events.append("clear")

    async def _hint() -> str | None:
        return "u@contoso.com"

    async def _run_signin(_provider) -> None:  # pragma: no cover - must never run
        events.append("interactive")

    monkeypatch.setattr(wp, "clear_workiq_oauth_tokens", _noop_clear)
    monkeypatch.setattr(wp, "get_workiq_login_hint", _hint)
    monkeypatch.setattr(wp, "_run_signin", _run_signin)

    # Silent success → authenticated, no OS browser, no interactive fallback.
    async def _silent_ok(*, login_hint, open_system_browser, callback_timeout=None) -> bool:
        events.append(f"silent(browser={open_system_browser},timeout={callback_timeout})")
        return True

    monkeypatch.setattr(wp, "_try_silent_reauth", _silent_ok)
    events.clear()
    assert await wp.reauthenticate_workiq(silent_only=True) is True
    assert events == [
        "clear",
        f"silent(browser=False,timeout={wp._SILENT_REAUTH_CALLBACK_TIMEOUT_SECONDS})",
    ]

    # Silent needs interaction → False, and the interactive prompt never runs.
    async def _silent_needs_ui(*, login_hint, open_system_browser, callback_timeout=None) -> bool:
        events.append("silent")
        return False

    monkeypatch.setattr(wp, "_try_silent_reauth", _silent_needs_ui)
    events.clear()
    assert await wp.reauthenticate_workiq(silent_only=True) is False
    assert events == ["clear", "silent"]

    # Any failure (timeout / framing blocked) is swallowed as "needs a human".
    async def _silent_boom(*, login_hint, open_system_browser, callback_timeout=None) -> bool:
        events.append("silent")
        raise RuntimeError("Timed out waiting for the WorkIQ sign-in to complete.")

    monkeypatch.setattr(wp, "_try_silent_reauth", _silent_boom)
    events.clear()
    assert await wp.reauthenticate_workiq(silent_only=True) is False
    assert events == ["clear", "silent"]


def test_callback_page_pending_status_is_neutral() -> None:
    """The interaction-required loopback page is calm and doesn't auto-close."""
    from precursor.backend.services.mcp import workiq_preview as wp

    html = wp._render_callback_page(
        status="pending", title="Finishing sign-in…", message="one moment"
    )
    assert "badge pending" in html
    assert "var autoClose = false" in html
    assert "var pending = true" in html

    app = create_app()
    with TestClient(app) as client:
        # Preview off by default → the endpoint refuses rather than opening a flow.
        resp = client.post("/api/mcp/servers/workiq/reauthenticate")
        assert resp.status_code == 400


def test_reauthenticate_runs_flow_and_reports_status(monkeypatch) -> None:
    from precursor.backend.services.mcp import workiq_preview as wp

    calls: list[bool] = []

    async def _fake_flow(*, open_system_browser: bool = True) -> None:
        calls.append(open_system_browser)

    monkeypatch.setattr(wp, "reauthenticate_workiq", _fake_flow)

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/mcp/servers/workiq/preview", json={"enabled": True})
        resp = client.post("/api/mcp/servers/workiq/reauthenticate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["preview"] is True
        assert body["transport"] == "streamable_http"
        # No popup flag → the server opens the OS browser as the fallback.
        assert calls == [True]

        # With a SPA-driven popup, the server skips the OS browser fallback.
        resp = client.post("/api/mcp/servers/workiq/reauthenticate?use_popup=true")
        assert resp.status_code == 200
        assert calls == [True, False]


def test_reauthenticate_silent_only_success(monkeypatch) -> None:
    """A hands-free silent pass that authenticates returns a normal status."""
    from precursor.backend.services.mcp import workiq_preview as wp

    calls: list[dict] = []

    async def _fake_flow(*, open_system_browser: bool = True, silent_only: bool = False) -> bool:
        calls.append({"open_system_browser": open_system_browser, "silent_only": silent_only})
        return True

    monkeypatch.setattr(wp, "reauthenticate_workiq", _fake_flow)

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/mcp/servers/workiq/preview", json={"enabled": True})
        resp = client.post("/api/mcp/servers/workiq/reauthenticate?silent_only=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["preview"] is True
        assert body.get("interaction_required") is not True
        # The endpoint drives the silent-only flow (which itself never opens an
        # OS browser tab); it doesn't pass open_system_browser through.
        assert len(calls) == 1
        assert calls[0]["silent_only"] is True


def test_reauthenticate_silent_only_needs_interaction(monkeypatch) -> None:
    """When the silent pass can't complete, the endpoint flags interaction_required."""
    from precursor.backend.services.mcp import workiq_preview as wp

    async def _fake_flow(*, open_system_browser: bool = True, silent_only: bool = False) -> bool:
        return False

    monkeypatch.setattr(wp, "reauthenticate_workiq", _fake_flow)

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/mcp/servers/workiq/preview", json={"enabled": True})
        resp = client.post("/api/mcp/servers/workiq/reauthenticate?silent_only=true")
        assert resp.status_code == 200
        assert resp.json()["interaction_required"] is True


def test_reauthenticate_silent_only_disabled_skips_flow(monkeypatch) -> None:
    """With auto re-auth off the endpoint reports interaction_required without a flow."""
    from precursor.backend import config as cfg
    from precursor.backend.services.mcp import workiq_preview as wp

    disabled = cfg.get_settings().model_copy(update={"workiq_auto_reauth_enabled": False})
    monkeypatch.setattr(cfg, "get_settings", lambda: disabled)

    called = False

    async def _fake_flow(*, open_system_browser: bool = True, silent_only: bool = False) -> bool:
        nonlocal called
        called = True  # pragma: no cover - must never run
        return True

    monkeypatch.setattr(wp, "reauthenticate_workiq", _fake_flow)

    app = create_app()
    with TestClient(app) as client:
        client.post("/api/mcp/servers/workiq/preview", json={"enabled": True})
        resp = client.post("/api/mcp/servers/workiq/reauthenticate?silent_only=true")
        assert resp.status_code == 200
        assert resp.json()["interaction_required"] is True
        assert called is False
