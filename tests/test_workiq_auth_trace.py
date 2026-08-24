"""WorkIQ auth observability — the trace, the diagnostics blob, token safety.

These cover the machinery that exists so a sign-in prompt can be *explained*
after the fact rather than guessed at: the credential-keyed trace and its
episodes, the redaction that makes it pasteable, the Entra error extraction that
finally captures why a refresh was refused, and the diagnostics endpoint that
hands the whole lot over in one request.

Also covers the behavioural half of the same problem: a hands-free pass that
fails must give the old credential back, because throwing away a refresh token
that was merely *suspected* dead is how a transient blip turns into a mandatory
interactive sign-in.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from mcp.shared.auth import OAuthToken

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting
from precursor.backend.services.mcp import auth_trace
from precursor.backend.services.mcp import workiq_preview as wp


@pytest.fixture(autouse=True)
def _clean_trace():
    auth_trace.reset()
    yield
    auth_trace.reset()


def test_records_carry_the_credential_not_the_server_name() -> None:
    """The Agent 365 pair shares one token, so it must share one story.

    Keying by server would split a single credential's episode across two
    timelines and make one sign-in look like two outages.
    """
    auth_trace.record("workiq-teams", "something happened")
    auth_trace.record("workiq-user", "something else happened")
    events = auth_trace.snapshot()
    assert {event["credential"] for event in events} == {"agent365_oauth_tokens"}


def test_episode_stitches_legs_together_and_closes() -> None:
    episode = auth_trace.begin_episode("workiq", "keep-alive gave up")
    # Re-entering keeps the same id: a hands-free pass following a keep-alive
    # failure is the same interruption, not a new one.
    assert auth_trace.begin_episode("workiq", "user clicked sign in") == episode
    auth_trace.record("workiq", "leg ① starting")
    assert auth_trace.current_episode("workiq") == episode

    auth_trace.end_episode("workiq", "renewed")
    assert auth_trace.current_episode("workiq") is None

    tagged = [event for event in auth_trace.snapshot() if event["episode"] == episode]
    assert [event["phase"] for event in tagged] == [
        "episode opened",
        "episode continues",
        "leg ① starting",
        "episode closed",
    ]
    # Every leg is timed against the episode start, so a 20s silent stall is
    # visible as a gap rather than having to be inferred from wall clocks.
    assert all(event["elapsed_ms"] is not None for event in tagged)


def test_secrets_are_reduced_to_a_shape_and_never_logged() -> None:
    """The buffer is meant to be pasted into a bug report as-is."""
    auth_trace.record(
        "workiq",
        "pretend grant",
        refresh_token="super-secret-value",
        login_hint="someone@contoso.com",
        code="",
        error_description="AADSTS700082: The refresh token has expired due to inactivity.",
    )
    detail = auth_trace.snapshot()[-1]["detail"]
    assert detail["refresh_token"] == "<present:18 chars>"
    assert detail["login_hint"] == "<present:19 chars>"
    assert detail["code"] == "<absent>"
    # ...but Entra's diagnosis is the whole point, so it survives intact.
    assert "AADSTS700082" in detail["error_description"]
    assert "super-secret-value" not in json.dumps(auth_trace.snapshot())


def test_trace_logs_to_its_own_channel(caplog: pytest.LogCaptureFixture) -> None:
    """``precursor.mcp.auth`` is grep-able independently of the app log level."""
    with caplog.at_level(logging.DEBUG, logger="precursor.mcp.auth"):
        auth_trace.record("workiq", "a thing happened")
    assert any("[workiq-auth]" in record.message for record in caplog.records)


async def test_oauth_error_facts_extracts_the_aadsts_code() -> None:
    """The datum the SDK discards — it logs only the status code."""
    import httpx

    response = httpx.Response(
        400,
        json={
            "error": "invalid_grant",
            "error_description": "AADSTS700082: The refresh token has expired due to inactivity.",
            "error_codes": [700082],
            "correlation_id": "abc-123",
            "trace_id": "def-456",
            "irrelevant": "ignored",
        },
    )
    facts = await wp._oauth_error_facts(response)
    assert facts["error"] == "invalid_grant"
    assert facts["error_codes"] == [700082]
    assert facts["correlation_id"] == "abc-123"
    assert "irrelevant" not in facts


async def test_oauth_error_facts_survives_a_non_json_body() -> None:
    """A captive portal or proxy interstitial is itself a diagnosis."""
    import httpx

    response = httpx.Response(502, text="<html>proxy denied</html>")
    facts = await wp._oauth_error_facts(response)
    assert "proxy denied" in facts["body_excerpt"]


def test_token_facts_never_include_the_token() -> None:
    token = OAuthToken(
        access_token="header.payload.signature",
        refresh_token="rt",
        token_type="Bearer",
        expires_in=3600,
        scope="https://example.com/.default offline_access",
    )
    facts = wp._token_facts(token)
    assert facts["has_refresh_token"] is True
    assert facts["expires_in"] == 3600
    assert "header.payload.signature" not in json.dumps(facts)


def test_scope_is_summarized_not_dumped() -> None:
    """Agent 365 grants ~37 fully-qualified scope URLs — about 4 KB of near
    identical text. Logging it verbatim buried every other field on the line and
    made the trace unreadable, which defeats the point of having one.
    """
    base = (
        "https://agent365.svc.cloud.microsoft/agents/tenants/"
        "72f988bf-86f1-41af-91ab-2d7cd011db47/servers/mcp_TeamsServer"
    )
    scope = " ".join(
        [f"{base}/McpServers.{name}.All" for name in ("Teams", "Mail", "Files")]
        + [f"{base}/.default", "offline_access"]
    )
    facts = wp._summarize_scope(scope)

    assert facts["scope_count"] == 5
    # The one scope fact that decides whether a credential is renewable at all.
    assert facts["scope_offline_access"] is True
    assert facts["scope_resource"] == base
    # The verbose list itself is gone.
    rendered = json.dumps(facts)
    assert "McpServers.Teams.All" not in rendered
    assert len(rendered) < 200


def test_scope_summary_flags_a_credential_that_can_never_renew() -> None:
    facts = wp._summarize_scope("https://example.com/.default")
    assert facts["scope_offline_access"] is False
    assert wp._summarize_scope(None) == {"scope": "<absent>"}


def test_provider_is_traced_and_the_sdk_hooks_still_exist() -> None:
    """Guard against an SDK upgrade silently blinding the trace."""
    from mcp.client.auth import OAuthClientProvider

    assert issubclass(wp._WorkIQOAuthClientProvider, OAuthClientProvider)
    for hook in wp._TRACED_SDK_HOOKS:
        assert hasattr(OAuthClientProvider, hook), hook
    assert isinstance(wp.build_oauth_provider(), wp._WorkIQOAuthClientProvider)


async def test_an_expired_stored_token_is_recognised_as_expired() -> None:
    """The bug that made every expiry cost an interactive sign-in.

    ``OAuthClientProvider._initialize`` restores the tokens but not their expiry,
    and ``is_token_valid()`` reads an unknown expiry as *valid*. So a token read
    back from storage always looked fresh, the refresh branch it guards was never
    entered, and the eventual 401 went straight to a browser grant — which never
    attempts a refresh either. A real trace showed 58 escalations to a full
    authorization and zero refresh attempts, against credentials that had a
    refresh token the whole time.
    """
    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens(reason="test setup")
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(access_token="a.b.c", refresh_token="rt", token_type="Bearer", expires_in=3600)
    )
    # Backdate the issue stamp so the stored token is two hours past expiry.
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    async with SessionLocal() as session:
        row = await session.get(AppSetting, wp.OAUTH_ISSUED_AT_KEY)
        assert row is not None
        row.value = json.dumps(stale)
        await session.commit()

    provider = wp.build_oauth_provider()
    await provider._initialize()

    assert provider.context.is_token_valid() is False
    # ...and the refresh path the SDK guards behind that is now reachable.
    assert provider.context.can_refresh_token() is True


async def test_a_live_stored_token_is_still_used_as_is() -> None:
    """The fix must not cause a needless refresh on every single request."""
    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens(reason="test setup")
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(access_token="a.b.c", refresh_token="rt", token_type="Bearer", expires_in=3600)
    )

    provider = wp.build_oauth_provider()
    await provider._initialize()

    assert provider.context.is_token_valid() is True


async def test_a_legacy_token_without_an_issue_stamp_is_left_alone() -> None:
    """No recorded issue time means no way to know — assume valid, as before.

    Forcing a sign-in for a token that may well still work would be a worse
    trade than one wasted request.
    """
    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens(reason="test setup")
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(access_token="a.b.c", refresh_token="rt", token_type="Bearer", expires_in=3600)
    )
    async with SessionLocal() as session:
        row = await session.get(AppSetting, wp.OAUTH_ISSUED_AT_KEY)
        assert row is not None
        await session.delete(row)
        await session.commit()

    provider = wp.build_oauth_provider()
    await provider._initialize()

    assert provider.context.token_expiry_time is None
    assert provider.context.is_token_valid() is True


async def test_a_refused_refresh_records_entras_reason() -> None:
    """The line that explains why the user is about to be interrupted.

    The SDK reaches this point, drops the tokens and falls through to a browser
    grant while logging only ``Token refresh failed: 400``.
    """
    import httpx

    provider = wp.build_oauth_provider()
    response = httpx.Response(
        400,
        json={
            "error": "invalid_grant",
            "error_description": "AADSTS50173: The provided grant has expired.",
            "error_codes": [50173],
        },
        request=httpx.Request("POST", "https://login.microsoftonline.com/common/oauth2/v2.0/token"),
    )

    assert await provider._handle_refresh_response(response) is False

    refusal = next(event for event in auth_trace.snapshot() if "REFUSED" in str(event["phase"]))
    assert refusal["detail"]["error"] == "invalid_grant"
    assert "AADSTS50173" in refusal["detail"]["error_description"]
    assert refusal["detail"]["purpose"] == "background"


async def test_clear_returns_the_rows_so_a_failed_pass_can_restore_them() -> None:
    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens(reason="test setup")
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(access_token="live", refresh_token="rt", token_type="Bearer", expires_in=3600)
    )

    removed = await wp.clear_workiq_oauth_tokens(reason="test")
    assert wp.OAUTH_TOKENS_KEY in removed
    assert await wp.DbTokenStorage().get_tokens() is None

    await wp.restore_workiq_oauth_tokens(wp.PREVIEW_PROFILE, removed, reason="test")
    restored = await wp.DbTokenStorage().get_tokens()
    assert restored is not None and restored.refresh_token == "rt"


async def test_restore_never_clobbers_a_newer_credential() -> None:
    """If the attempt *did* land a token, the fresh one wins."""
    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens(reason="test setup")
    await wp.DbTokenStorage().set_tokens(OAuthToken(access_token="old", token_type="Bearer"))
    removed = await wp.clear_workiq_oauth_tokens(reason="test")
    await wp.DbTokenStorage().set_tokens(OAuthToken(access_token="new", token_type="Bearer"))

    await wp.restore_workiq_oauth_tokens(wp.PREVIEW_PROFILE, removed, reason="test")
    tokens = await wp.DbTokenStorage().get_tokens()
    assert tokens is not None and tokens.access_token == "new"


async def test_failed_hands_free_pass_hands_the_credential_back(monkeypatch) -> None:
    """A suspected-dead refresh token that was fine must not be destroyed.

    The verdict that triggers a hands-free pass can come from a transient 401.
    Before this, the pass cleared the tokens up front and a failure left nothing
    behind — so the user *had* to sign in interactively even though the stored
    credential would have refreshed perfectly well on the next try.
    """
    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens(reason="test setup")
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(
            access_token="still-good", refresh_token="rt", token_type="Bearer", expires_in=3600
        )
    )

    async def _hint(*_a, **_k) -> str | None:
        return "u@contoso.com"

    async def _silent_needs_interaction(**_k) -> bool:
        return False

    monkeypatch.setattr(wp, "_bind_loopback_profile", lambda p: p)
    monkeypatch.setattr(wp, "get_workiq_login_hint", _hint)
    monkeypatch.setattr(wp, "_try_silent_reauth", _silent_needs_interaction)

    assert await wp.reauthenticate_workiq(silent_only=True) is False

    tokens = await wp.DbTokenStorage().get_tokens()
    assert tokens is not None and tokens.refresh_token == "rt"


async def test_interactive_signin_still_clears_outright(monkeypatch) -> None:
    """The user is right there — a stale token must not shadow the new grant."""
    with TestClient(create_app()):
        pass

    await wp.clear_workiq_oauth_tokens(reason="test setup")
    await wp.DbTokenStorage().set_tokens(
        OAuthToken(access_token="stale", refresh_token="rt", token_type="Bearer", expires_in=3600)
    )

    async def _hint(*_a, **_k) -> str | None:
        return None

    async def _run_signin(_provider, _profile=None) -> None:
        return None

    monkeypatch.setattr(wp, "_bind_loopback_profile", lambda p: p)
    monkeypatch.setattr(wp, "get_workiq_login_hint", _hint)
    monkeypatch.setattr(wp, "_run_signin", _run_signin)

    assert await wp.reauthenticate_workiq(open_system_browser=False) is True
    assert await wp.DbTokenStorage().get_tokens() is None


def test_diagnostics_reports_settings_credentials_and_events() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Preview mode on, so the WorkIQ preview credential is signable and shows up.
        client.post("/api/mcp/servers/workiq/preview", json={"enabled": True})
        auth_trace.record("workiq", "a traced step", reason="test")

        try:
            body = client.get("/api/mcp/auth/diagnostics").json()
        finally:
            # The preview flag is persisted, and the shared test DB outlives this
            # test — leave it as we found it.
            client.post("/api/mcp/servers/workiq/preview", json={"enabled": False})

    assert body["settings"]["workiq_auto_reauth_enabled"] is True
    assert body["settings"]["workiq_auth_log_level"]
    preview = next(c for c in body["credentials"] if c["server"] == "workiq")
    # The three facts a lapse always turns on, none of which were previously
    # readable from outside the process.
    assert preview["has_tokens"] is False
    assert preview["has_refresh_token"] is False
    assert preview["expires_in_seconds"] is None
    assert preview["credential"] == wp.OAUTH_TOKENS_KEY
    assert any(event["phase"] == "a traced step" for event in body["events"])


def test_diagnostics_limit_caps_the_event_window() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Reset *after* startup: booting the app can itself trace (the keep-alive
        # takes its first tick immediately), and this test is about the window.
        auth_trace.reset()
        for index in range(10):
            auth_trace.record("workiq", f"step {index}")
        body = client.get("/api/mcp/auth/diagnostics?limit=3").json()
    assert [event["phase"] for event in body["events"]] == ["step 7", "step 8", "step 9"]


def test_ambient_chatter_cannot_evict_an_episode() -> None:
    """The keep-alive heartbeat runs forever; an episode must outlive it.

    Sharing one ring buffer meant a once-a-minute tick turned it over in hours —
    so by the time anyone looked into a prompt they'd been given overnight, the
    lines explaining it were long gone and all that remained was the heartbeat.
    """
    auth_trace.begin_episode("workiq", "keep-alive could not renew silently")
    auth_trace.record("workiq", "silent refresh REFUSED by Entra", error="invalid_grant")
    auth_trace.end_episode("workiq", "gave up")

    # Far more ambient records than either buffer holds.
    for index in range(auth_trace._AMBIENT_TRACE_LIMIT * 3):
        auth_trace.record("workiq", f"keep-alive heartbeat {index}")

    events = auth_trace.snapshot()
    assert any("REFUSED" in event["phase"] for event in events)
    # The ambient ring rolled over as designed — early heartbeats are gone...
    phases = [event["phase"] for event in events]
    assert "keep-alive heartbeat 0" not in phases
    # ...while the merged timeline still reads in order, episode first.
    last_heartbeat = f"keep-alive heartbeat {auth_trace._AMBIENT_TRACE_LIMIT * 3 - 1}"
    assert phases.index("silent refresh REFUSED by Entra") < phases.index(last_heartbeat)


async def test_keepalive_reports_a_verdict_once_not_every_tick(monkeypatch) -> None:
    """Only transitions are worth recording; a steady state is not news."""
    from precursor.backend.services.mcp import workiq_keepalive as ka

    keepalive = ka.WorkIQKeepAlive()

    async def _no_tokens(*_a, **_k):
        return None

    monkeypatch.setattr(
        ka, "DbTokenStorage", lambda _profile: type("_S", (), {"get_tokens": _no_tokens})()
    )

    for _ in range(5):
        await keepalive._tick_profile(wp.PREVIEW_PROFILE)

    recorded = [e for e in auth_trace.snapshot() if "nothing stored" in e["phase"]]
    assert len(recorded) == 1
