"""WorkIQ keep-alive ticker — refresh-decision + auth-banner edge-trigger.

These drive ``_tick_once`` directly with the WorkIQ preview seams monkeypatched,
so they exercise the keep-alive's decision logic (when to refresh, when to raise
the re-auth banner) without any real OAuth/network or a running event loop task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mcp.shared.auth import OAuthToken

from precursor.backend.services.mcp import agent365
from precursor.backend.services.mcp import workiq_keepalive as ka
from precursor.backend.services.mcp import workiq_preview as wp
from precursor.backend.services.mcp.usage import reset_usage


class _FakeStorage:
    """Stand-in for ``DbTokenStorage`` returning a fixed token (or none)."""

    # A real ``OAuthToken`` rather than a sentinel: the ticker reads
    # ``refresh_token`` off it to decide whether a refresh can succeed at all.
    token: OAuthToken | None = OAuthToken(access_token="tok", refresh_token="rt")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def get_tokens(self) -> OAuthToken | None:
        return type(self).token


class _FakeManager:
    """Records the short-circuit verdict the keep-alive feeds the client pool."""

    def __init__(self) -> None:
        self.marked: list[str] = []
        self.cleared: list[str] = []

    def mark_auth_required(self, name: str, *, message: str | None = None) -> None:
        self.marked.append(name)

    def clear_auth_required(self, name: str) -> None:
        self.cleared.append(name)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch the preview seams the ticker calls and record refresh calls."""
    state: dict = {
        "preview": True,
        "expiry": None,
        "refresh_result": ("tok", None),
        "refresh_calls": 0,
        "auth_banner_calls": [],
        "manager": _FakeManager(),
    }
    _FakeStorage.token = OAuthToken(access_token="tok", refresh_token="rt")
    reset_usage()

    async def _resolve_preview() -> bool:
        return state["preview"]

    async def _stored_expiry(_token: object, _profile: object = None) -> datetime | None:
        return state["expiry"]

    async def _resolve_bearer(_profile: object = None) -> tuple[str, datetime | None] | None:
        state["refresh_calls"] += 1
        return state["refresh_result"]

    async def _publish(server: str, message: str, *, topic_id: int | None = None) -> None:
        state["auth_banner_calls"].append(server)

    # Preview mode is resolved through the OAuth registry, which dereferences
    # the module attribute at call time — so patch it on its owning module.
    monkeypatch.setattr(wp, "resolve_workiq_preview", _resolve_preview)
    monkeypatch.setattr(ka, "DbTokenStorage", _FakeStorage)
    monkeypatch.setattr(ka, "_stored_token_expiry", _stored_expiry)
    monkeypatch.setattr(ka, "resolve_workiq_bearer_token", _resolve_bearer)
    monkeypatch.setattr(ka, "publish_mcp_auth_required", _publish)
    monkeypatch.setattr(ka, "get_mcp_client_manager", lambda: state["manager"])
    # The Agent 365 profiles resolve their tenant from the DB; keep this unit
    # test on the preview profile alone.
    monkeypatch.setattr(agent365, "AGENT365_SERVERS", ())
    return state


async def test_skips_when_preview_disabled(patched: dict) -> None:
    patched["preview"] = False
    await ka.WorkIQKeepAlive()._tick_once()
    assert patched["refresh_calls"] == 0


async def test_skips_when_no_tokens(patched: dict) -> None:
    _FakeStorage.token = None
    await ka.WorkIQKeepAlive()._tick_once()
    assert patched["refresh_calls"] == 0


async def test_skips_refresh_when_token_still_fresh(patched: dict) -> None:
    keepalive = ka.WorkIQKeepAlive()
    margin = keepalive._settings.workiq_keepalive_refresh_margin_seconds
    patched["expiry"] = datetime.now(UTC) + timedelta(seconds=margin + 120)
    await keepalive._tick_once()
    assert patched["refresh_calls"] == 0


async def test_refreshes_when_token_near_expiry(patched: dict) -> None:
    keepalive = ka.WorkIQKeepAlive()
    margin = keepalive._settings.workiq_keepalive_refresh_margin_seconds
    patched["expiry"] = datetime.now(UTC) + timedelta(seconds=margin - 30)
    await keepalive._tick_once()
    assert patched["refresh_calls"] == 1
    assert patched["auth_banner_calls"] == []


async def test_refreshes_when_expiry_unknown(patched: dict) -> None:
    # Legacy token with no derivable expiry → refresh anyway.
    patched["expiry"] = None
    keepalive = ka.WorkIQKeepAlive()
    await keepalive._tick_once()
    assert patched["refresh_calls"] == 1


async def test_skips_idle_credential_entirely(patched: dict, monkeypatch) -> None:
    """A credential nobody has used is neither refreshed nor prompted for.

    This is the noisiest prompt we used to raise: a server enabled long ago and
    never called would eventually fail its silent refresh and ask the user to
    sign in for tools they weren't using.
    """
    from precursor.backend.services.mcp import usage

    patched["expiry"] = None  # unknown expiry → would refresh if not idle
    patched["refresh_result"] = None  # ...and would then raise the banner
    keepalive = ka.WorkIQKeepAlive()
    idle_after = keepalive._settings.workiq_keepalive_idle_after_seconds
    assert idle_after > 0

    base = usage.time.monotonic()
    monkeypatch.setattr(usage.time, "monotonic", lambda: base + idle_after + 60)

    await keepalive._tick_once()
    assert patched["refresh_calls"] == 0
    assert patched["auth_banner_calls"] == []

    # Calling any tool on it marks it active again, so the next tick resumes.
    usage.mark_server_used("workiq")
    await keepalive._tick_once()
    assert patched["refresh_calls"] == 1


def _force_idle(keepalive: ka.WorkIQKeepAlive, monkeypatch) -> None:
    """Push the usage clock past the idle window so every credential reads idle."""
    from precursor.backend.services.mcp import usage

    idle_after = keepalive._settings.workiq_keepalive_idle_after_seconds
    base = usage.time.monotonic()
    monkeypatch.setattr(usage.time, "monotonic", lambda: base + idle_after + 60)


async def test_surfaces_idle_lapse_when_token_expired(patched: dict, monkeypatch) -> None:
    """An idle credential whose token has actually expired is surfaced.

    Unlike the unknown-expiry idle case (left alone), a demonstrably expired
    access token is probed once; a dead refresh token raises the banner and flags
    the turn path so the next request fast-fails instead of stalling.
    """
    patched["expiry"] = datetime.now(UTC) - timedelta(minutes=5)  # already expired
    patched["refresh_result"] = None  # silent refresh needs interactive sign-in
    keepalive = ka.WorkIQKeepAlive()
    _force_idle(keepalive, monkeypatch)

    await keepalive._tick_once()
    await keepalive._tick_once()  # still failing, but already notified

    assert patched["refresh_calls"] == 1  # probed once, then skipped via the latch
    assert patched["auth_banner_calls"] == ["workiq"]
    assert patched["manager"].marked == ["workiq"]


async def test_recovers_idle_lapse_silently(patched: dict, monkeypatch) -> None:
    """An idle credential that still refreshes clears the verdict, no banner."""
    patched["expiry"] = datetime.now(UTC) - timedelta(minutes=5)
    patched["refresh_result"] = ("tok", None)  # silent refresh still works
    keepalive = ka.WorkIQKeepAlive()
    _force_idle(keepalive, monkeypatch)

    await keepalive._tick_once()

    assert patched["refresh_calls"] == 1
    assert patched["auth_banner_calls"] == []
    assert patched["manager"].cleared == ["workiq"]


async def test_idle_lapse_left_alone_when_expiry_unknown(patched: dict, monkeypatch) -> None:
    """A legacy idle token with no derivable expiry is not probed or prompted."""
    patched["expiry"] = None
    patched["refresh_result"] = None
    keepalive = ka.WorkIQKeepAlive()
    _force_idle(keepalive, monkeypatch)

    await keepalive._tick_once()

    assert patched["refresh_calls"] == 0
    assert patched["auth_banner_calls"] == []
    assert patched["manager"].marked == []


async def test_idle_lapse_opt_out(patched: dict, monkeypatch) -> None:
    """Disabling the knob keeps idle credentials completely silent."""
    patched["expiry"] = datetime.now(UTC) - timedelta(minutes=5)
    patched["refresh_result"] = None
    keepalive = ka.WorkIQKeepAlive()
    keepalive._settings = keepalive._settings.model_copy(
        update={"workiq_keepalive_surface_idle_lapse": False}
    )
    _force_idle(keepalive, monkeypatch)

    await keepalive._tick_once()

    assert patched["refresh_calls"] == 0
    assert patched["auth_banner_calls"] == []
    assert patched["manager"].marked == []


async def test_active_refresh_failure_flags_manager(patched: dict) -> None:
    """A failed refresh for an active credential also feeds the manager verdict."""
    patched["expiry"] = None
    patched["refresh_result"] = None
    keepalive = ka.WorkIQKeepAlive()

    await keepalive._tick_once()

    assert patched["manager"].marked == ["workiq"]


async def test_raises_auth_banner_once_when_refresh_fails(patched: dict) -> None:
    patched["expiry"] = None
    patched["refresh_result"] = None  # silent refresh needs interactive sign-in
    keepalive = ka.WorkIQKeepAlive()

    await keepalive._tick_once()
    await keepalive._tick_once()  # still failing

    # Edge-triggered: the banner is published once, not every tick.
    assert patched["auth_banner_calls"] == ["workiq"]
    assert patched["refresh_calls"] == 2


async def test_auth_banner_rearms_after_recovery(patched: dict) -> None:
    patched["expiry"] = None
    keepalive = ka.WorkIQKeepAlive()

    patched["refresh_result"] = None
    await keepalive._tick_once()  # fail → publish
    patched["refresh_result"] = ("tok", None)
    await keepalive._tick_once()  # recover → clears the latch
    patched["refresh_result"] = None
    await keepalive._tick_once()  # fail again → publish again

    assert patched["auth_banner_calls"] == ["workiq", "workiq"]


async def test_no_refresh_token_prompts_without_a_round_trip(patched: dict) -> None:
    """A credential stored without a refresh token can't be renewed — say so.

    Every WorkIQ sign-in predating the ``offline_access`` request produced one of
    these. There is nothing to refresh *with*, so attempting the round trip only
    burns a request to reach the same conclusion; go straight to the banner,
    which is also the upgrade path (the sign-in it asks for mints a renewable
    credential).
    """
    _FakeStorage.token = OAuthToken(access_token="tok", refresh_token=None)
    keepalive = ka.WorkIQKeepAlive()
    margin = keepalive._settings.workiq_keepalive_refresh_margin_seconds
    patched["expiry"] = datetime.now(UTC) + timedelta(seconds=margin - 30)

    await keepalive._tick_once()

    assert patched["refresh_calls"] == 0  # no doomed round trip
    assert patched["auth_banner_calls"] == ["workiq"]
    assert patched["manager"].marked == ["workiq"]


async def test_no_refresh_token_still_refreshes_when_expiry_unknown(patched: dict) -> None:
    """A legacy token with no derivable expiry is probed, not pre-emptively failed.

    We only know such a token is *due* for refresh by convention, not fact — it
    may well still be valid — so the short-circuit deliberately stays out of the
    way and lets the real probe decide.
    """
    _FakeStorage.token = OAuthToken(access_token="tok", refresh_token=None)
    patched["expiry"] = None

    await ka.WorkIQKeepAlive()._tick_once()

    assert patched["refresh_calls"] == 1
    assert patched["auth_banner_calls"] == []


async def test_idle_lapse_without_refresh_token_prompts_directly(
    patched: dict, monkeypatch
) -> None:
    """Same short-circuit on the idle path, which only runs once expiry has passed."""
    _FakeStorage.token = OAuthToken(access_token="tok", refresh_token=None)
    patched["expiry"] = datetime.now(UTC) - timedelta(minutes=5)
    keepalive = ka.WorkIQKeepAlive()
    _force_idle(keepalive, monkeypatch)

    await keepalive._tick_once()

    assert patched["refresh_calls"] == 0
    assert patched["auth_banner_calls"] == ["workiq"]
