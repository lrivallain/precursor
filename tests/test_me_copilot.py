"""The persona menu's Copilot "AI credits" endpoint (``GET /api/me/copilot``).

The bar is fed by the ``premium_interactions`` quota snapshot from GitHub's
``/copilot_internal/user`` endpoint. These tests pin the normalizer (only the
metered snapshot becomes a bar; percentages are derived and clamped) and the
route's graceful degradation — ``null`` for guests and seatless accounts, never
an error — so the persona quietly hides the bar instead of breaking.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.routers import me as me_router
from precursor.backend.routers.me import _normalize_quota


@pytest.fixture(autouse=True)
def _clear_quota_cache() -> Any:
    """The quota cache is module-level; don't leak a token's result across tests."""
    me_router._quota_cache.clear()
    yield
    me_router._quota_cache.clear()


def _payload(**premium: Any) -> dict[str, Any]:
    """A ``/copilot_internal/user`` payload with a premium_interactions snapshot."""
    return {
        "copilot_plan": "enterprise",
        "quota_reset_date": "2026-09-01",
        "quota_snapshots": {
            "chat": {"unlimited": True},
            "completions": {"unlimited": True},
            "premium_interactions": {
                "unlimited": False,
                "percent_remaining": 99.5,
                "credits_used": 49309,
                "entitlement": 10000000,
                "remaining": 9950690,
                **premium,
            },
        },
    }


def test_normalize_maps_premium_snapshot() -> None:
    quota = _normalize_quota(_payload())
    assert quota is not None
    assert quota.plan == "enterprise"
    assert quota.unlimited is False
    # percent_used is derived from percent_remaining and rounded to 1 decimal.
    assert quota.percent_used == 0.5
    assert quota.percent_remaining == 99.5
    assert quota.used == 49309
    assert quota.entitlement == 10000000
    assert quota.remaining == 9950690
    assert quota.reset_date == "2026-09-01"


def test_normalize_clamps_out_of_range_percent() -> None:
    over = _normalize_quota(_payload(percent_remaining=150.0))
    assert over is not None
    assert over.percent_remaining == 100.0
    assert over.percent_used == 0.0
    under = _normalize_quota(_payload(percent_remaining=-5.0))
    assert under is not None
    assert under.percent_remaining == 0.0
    assert under.percent_used == 100.0


def test_normalize_returns_none_without_premium_snapshot() -> None:
    # A seat with no metered allowance (only unlimited chat/completions) → no bar.
    payload = {"quota_snapshots": {"chat": {"unlimited": True}}}
    assert _normalize_quota(payload) is None
    assert _normalize_quota({}) is None


def test_endpoint_returns_null_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_token(_session: Any) -> str | None:
        return None

    monkeypatch.setattr(me_router, "resolve_github_token", _no_token)
    with TestClient(create_app()) as client:
        resp = client.get("/api/me/copilot")
    assert resp.status_code == 200
    assert resp.json() is None


def test_endpoint_returns_quota_for_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _token(_session: Any) -> str | None:
        return "tok-123"

    async def _quota(self: Any) -> dict[str, Any]:
        return _payload()

    monkeypatch.setattr(me_router, "resolve_github_token", _token)
    monkeypatch.setattr(me_router.GitHubClient, "get_copilot_quota", _quota)
    with TestClient(create_app()) as client:
        resp = client.get("/api/me/copilot")
    assert resp.status_code == 200
    body = resp.json()
    assert body is not None
    assert body["percent_used"] == 0.5
    assert body["reset_date"] == "2026-09-01"


def test_endpoint_returns_null_for_seatless_account(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _token(_session: Any) -> str | None:
        return "tok-456"

    async def _no_seat(self: Any) -> dict[str, Any] | None:
        # GitHubClient maps a 403/404 (no Copilot seat) to None.
        return None

    monkeypatch.setattr(me_router, "resolve_github_token", _token)
    monkeypatch.setattr(me_router.GitHubClient, "get_copilot_quota", _no_seat)
    with TestClient(create_app()) as client:
        resp = client.get("/api/me/copilot")
    assert resp.status_code == 200
    assert resp.json() is None
