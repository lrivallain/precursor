"""WorkIQ keep-alive ticker — refresh-decision + auth-banner edge-trigger.

These drive ``_tick_once`` directly with the WorkIQ preview seams monkeypatched,
so they exercise the keep-alive's decision logic (when to refresh, when to raise
the re-auth banner) without any real OAuth/network or a running event loop task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from precursor.backend.services.mcp import workiq_keepalive as ka


class _FakeStorage:
    """Stand-in for ``DbTokenStorage`` returning a fixed token (or none)."""

    token: object | None = object()

    async def get_tokens(self) -> object | None:
        return type(self).token


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch the preview seams the ticker calls and record refresh calls."""
    state: dict = {
        "preview": True,
        "expiry": None,
        "refresh_result": ("tok", None),
        "refresh_calls": 0,
        "auth_banner_calls": [],
    }
    _FakeStorage.token = object()

    async def _resolve_preview() -> bool:
        return state["preview"]

    async def _stored_expiry(_token: object) -> datetime | None:
        return state["expiry"]

    async def _resolve_bearer() -> tuple[str, datetime | None] | None:
        state["refresh_calls"] += 1
        return state["refresh_result"]

    async def _publish(server: str, message: str, *, topic_id: int | None = None) -> None:
        state["auth_banner_calls"].append(server)

    monkeypatch.setattr(ka, "resolve_workiq_preview", _resolve_preview)
    monkeypatch.setattr(ka, "DbTokenStorage", _FakeStorage)
    monkeypatch.setattr(ka, "_stored_token_expiry", _stored_expiry)
    monkeypatch.setattr(ka, "resolve_workiq_bearer_token", _resolve_bearer)
    monkeypatch.setattr(ka, "publish_mcp_auth_required", _publish)
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
