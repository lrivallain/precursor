"""A deliberate renewal must actually renew.

Two thresholds decide "renew this token": the keep-alive's renewal lead, which
opens the session, and the SDK's ``is_token_valid()``, which gates the only
refresh branch ``async_auth_flow`` has. Between them a token that is inside the
renewal window but not yet expired gets a session opened for it, no refresh
performed, and the unchanged token read back as if it had been renewed — so the
credential is only ever really renewed in the last seconds of its life.

``renew_now`` closes that gap by stating the intent the caller already acted on.
It is deliberately *not* set on the chat-turn / catalog-probe / warm-pool
providers: an invalidated token whose refresh then fails transiently is sent with
no auth header at all, and the resulting 401 escalates to a browser grant.
"""

from __future__ import annotations

import time

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

import precursor.backend.services.mcp.workiq_preview as wp

# Longer than the SDK's own expiry skew, so a provider that merely restored the
# stored expiry would still consider this token perfectly valid.
INSIDE_RENEWAL_WINDOW_SECONDS = 240


def _provider(*, renew_now: bool) -> wp.OAuthClientProvider:
    """A provider holding a renewable credential, short of any network call."""
    provider = wp.build_oauth_provider(
        profile=wp.PREVIEW_PROFILE, interactive=False, renew_now=renew_now
    )
    # ``_initialize`` is the seam under test and it loads from storage, so give
    # it an in-memory one rather than a database.
    provider.context.storage = _MemoryStorage()
    return provider


class _MemoryStorage:
    """``TokenStorage`` holding a renewable credential, with no database."""

    def __init__(self) -> None:
        self._tokens = OAuthToken(
            access_token="stale", refresh_token="renewable", token_type="Bearer"
        )
        self._client_info = OAuthClientInformationFull(
            client_id=wp.PREVIEW_PROFILE.client_id,
            redirect_uris=[wp.PREVIEW_PROFILE.redirect_uri],
            token_endpoint_auth_method="none",
        )

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_info = client_info


@pytest.fixture
def _primed(monkeypatch: pytest.MonkeyPatch):
    """Pin the stored expiry inside the renewal window, and skip discovery."""

    async def _expiry(_token, _profile=None):
        from datetime import UTC, datetime, timedelta

        return datetime.now(UTC) + timedelta(seconds=INSIDE_RENEWAL_WINDOW_SECONDS)

    async def _no_discovery(_url: str):
        return None

    monkeypatch.setattr(wp, "_stored_token_expiry", _expiry)
    monkeypatch.setattr(wp, "_discover_authorization_server", _no_discovery)


@pytest.mark.anyio
async def test_renew_now_makes_the_sdk_refresh_a_still_valid_token(_primed) -> None:
    provider = _provider(renew_now=True)
    await provider._initialize()

    # ``async_auth_flow`` guards its only refresh branch on exactly this.
    assert provider.context.is_token_valid() is False
    assert provider.context.can_refresh_token() is True


@pytest.mark.anyio
async def test_a_normal_provider_leaves_a_still_valid_token_alone(_primed) -> None:
    """The chat-turn / warm-pool default, which must keep its auth header."""
    provider = _provider(renew_now=False)
    await provider._initialize()

    assert provider.context.is_token_valid() is True


@pytest.mark.anyio
async def test_renew_now_survives_an_unknown_expiry(monkeypatch) -> None:
    """A legacy token with no derivable expiry is renewed too, not assumed fresh.

    Restoring the expiry is what normally makes the SDK's refresh branch
    reachable; a token that has no recorded expiry deliberately keeps the SDK's
    assume-valid behaviour, which would otherwise veto a renewal the caller has
    already decided on.
    """

    async def _no_expiry(_token, _profile=None):
        return None

    async def _no_discovery(_url: str):
        return None

    monkeypatch.setattr(wp, "_stored_token_expiry", _no_expiry)
    monkeypatch.setattr(wp, "_discover_authorization_server", _no_discovery)

    provider = _provider(renew_now=True)
    await provider._initialize()

    assert provider.context.is_token_valid() is False
    # Zero is falsy, and the SDK reads a falsy expiry as "unknown, so valid".
    assert provider.context.token_expiry_time
    assert provider.context.token_expiry_time < time.time()


@pytest.mark.anyio
async def test_a_successful_refresh_clears_the_forced_expiry(_primed) -> None:
    """The forced expiry must not outlive the refresh it was there to trigger.

    It is set once, in ``_initialize``; the request that follows the refresh only
    gets an auth header while ``is_token_valid()``, so a stale forced value would
    strip the header off the very request the renewal was for.
    """

    class _Response:
        status_code = 200

        async def aread(self):
            return b'{"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}'

    provider = _provider(renew_now=True)
    await provider._initialize()

    assert await provider._handle_refresh_response(_Response()) is True
    assert provider.context.is_token_valid() is True


@pytest.mark.parametrize(
    ("expires_in", "expected"),
    [
        (None, 300.0),  # legacy token, no lifetime recorded
        (0, 300.0),  # nonsense lifetime, treated as unknown
        (600, 300.0),  # short-lived: the floor wins
        (4200, 1050.0),  # a real Entra token: ~17 minutes of runway
        (5400, 1350.0),
    ],
)
def test_renewal_lead_is_derived_from_the_token_lifetime(expires_in, expected) -> None:
    token = OAuthToken(access_token="tok", token_type="Bearer", expires_in=expires_in)

    assert wp.renewal_lead_seconds(token) == expected


def test_renewal_lead_falls_back_without_a_token() -> None:
    assert wp.renewal_lead_seconds(None) == 300.0
