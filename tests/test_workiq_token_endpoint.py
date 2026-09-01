"""The silent refresh must reach Entra, not the resource host.

``async_auth_flow`` runs its refresh branch *before* the discovery it performs in
the 401 branch, so a freshly built provider — which every background renewal is —
reaches ``_refresh_token`` with no ``oauth_metadata``. The SDK then falls back to
``urljoin(server_url, "/token")`` and POSTs the grant at the WorkIQ/Agent 365
host, which answers 400/404; the SDK reads that as a refused refresh, drops the
tokens and escalates to a browser sign-in. These pin the seam that prevents it.
"""

from __future__ import annotations

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

import precursor.backend.services.mcp.workiq_preview as wp

ENTRA_TOKEN_ENDPOINT = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"

AGENT365_PROFILE = wp.PREVIEW_PROFILE.__class__(
    server="workiq-teams",
    label="WorkIQ Teams",
    url="https://agent365.svc.cloud.microsoft/agents/tenants/tid/servers/mcp_TeamsServer",
    client_id="client",
    redirect_port=12799,
    client_name="Precursor (test)",
    tokens_key="agent365_oauth_tokens",
    issued_at_key="agent365_oauth_issued_at",
    login_hint_key="agent365_oauth_login_hint",
)


def _entra_metadata() -> OAuthMetadata:
    return OAuthMetadata(
        issuer="https://login.microsoftonline.com/organizations/v2.0",
        authorization_endpoint="https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
        token_endpoint=ENTRA_TOKEN_ENDPOINT,
        response_types_supported=["code"],
    )


def _primed(profile) -> wp.OAuthClientProvider:
    """A provider holding a renewable credential, short of any network call."""
    provider = wp.build_oauth_provider(profile=profile, interactive=False)
    provider.context.current_tokens = OAuthToken(
        access_token="stale", refresh_token="renewable", token_type="Bearer"
    )
    provider.context.client_info = OAuthClientInformationFull(
        client_id=profile.client_id,
        redirect_uris=[profile.redirect_uri],
        token_endpoint_auth_method="none",
    )
    return provider


@pytest.fixture(autouse=True)
def _clear_metadata_cache():
    wp._ASM_CACHE.clear()
    wp._ASM_FAILED_UNTIL.clear()
    yield
    wp._ASM_CACHE.clear()
    wp._ASM_FAILED_UNTIL.clear()


@pytest.mark.parametrize("profile", [wp.PREVIEW_PROFILE, AGENT365_PROFILE])
@pytest.mark.anyio
async def test_refresh_targets_entra_once_seeded(profile, monkeypatch) -> None:
    async def _fake_discovery(_url: str) -> OAuthMetadata:
        return _entra_metadata()

    monkeypatch.setattr(wp, "_discover_authorization_server", _fake_discovery)

    provider = _primed(profile)
    await provider._seed_authorization_server()
    request = await provider._refresh_token()

    assert str(request.url) == ENTRA_TOKEN_ENDPOINT


@pytest.mark.parametrize("profile", [wp.PREVIEW_PROFILE, AGENT365_PROFILE])
@pytest.mark.anyio
async def test_unseeded_refresh_would_hit_the_resource_host(profile) -> None:
    """The bug this guards against, asserted against the SDK's own fallback.

    If a future SDK stops deriving the token URL from the server URL this fails,
    which is the signal that the seeding above is no longer load-bearing.
    """
    request = await _primed(profile)._refresh_token()

    assert str(request.url).startswith(
        profile.url.split("/", 3)[0] + "//" + profile.url.split("/")[2]
    )
    assert str(request.url).endswith("/token")


@pytest.mark.anyio
async def test_seeding_leaves_the_refresh_body_untouched(monkeypatch) -> None:
    """Only the URL changes.

    Seeding ``protected_resource_metadata`` too would flip
    ``should_include_resource_param`` and add an RFC 8707 ``resource`` field to
    the grant. We deliberately seed only the authorization-server metadata.
    """

    async def _fake_discovery(_url: str) -> OAuthMetadata:
        return _entra_metadata()

    monkeypatch.setattr(wp, "_discover_authorization_server", _fake_discovery)

    provider = _primed(wp.PREVIEW_PROFILE)
    await provider._seed_authorization_server()
    request = await provider._refresh_token()

    fields = {pair.split("=")[0] for pair in request.content.decode().split("&")}
    assert fields == {"grant_type", "refresh_token", "client_id"}
    assert provider.context.protected_resource_metadata is None


@pytest.mark.anyio
async def test_failed_discovery_leaves_sdk_behaviour_intact(monkeypatch) -> None:
    """A discovery outage must not make things worse than before the fix."""

    async def _no_metadata(_url: str) -> None:
        return None

    monkeypatch.setattr(wp, "_discover_authorization_server", _no_metadata)

    provider = _primed(wp.PREVIEW_PROFILE)
    await provider._seed_authorization_server()

    assert provider.context.oauth_metadata is None
    request = await provider._refresh_token()
    assert str(request.url) == "https://workiq.svc.cloud.microsoft/token"


@pytest.mark.anyio
async def test_discovery_is_resolved_once_per_endpoint(monkeypatch) -> None:
    """Two round trips per provider would be paid on every keep-alive tick."""
    calls: list[str] = []

    class _Response:
        status_code = 200

    async def _fake_send(self, request):
        calls.append(str(request.url))
        return _Response()

    async def _prm(response):
        return type(
            "PRM",
            (),
            {"authorization_servers": ["https://login.microsoftonline.com/organizations/v2.0"]},
        )()

    async def _asm(response):
        return True, _entra_metadata()

    monkeypatch.setattr(wp.httpx.AsyncClient, "send", _fake_send)
    monkeypatch.setattr(wp, "handle_protected_resource_response", _prm)
    monkeypatch.setattr(wp, "handle_auth_metadata_response", _asm)

    first = await wp._discover_authorization_server(wp.PREVIEW_PROFILE.url)
    after_first = len(calls)
    second = await wp._discover_authorization_server(wp.PREVIEW_PROFILE.url)

    assert str(first.token_endpoint) == ENTRA_TOKEN_ENDPOINT
    assert second is first
    assert len(calls) == after_first


@pytest.mark.anyio
async def test_unreachable_endpoint_is_not_retried_every_tick(monkeypatch) -> None:
    """A hung network must not cost the keep-alive a timeout every 60 seconds."""
    attempts: list[str] = []

    async def _fake_send(self, request):
        attempts.append(str(request.url))
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(wp.httpx.AsyncClient, "send", _fake_send)

    assert await wp._discover_authorization_server(wp.PREVIEW_PROFILE.url) is None
    after_first = len(attempts)
    assert after_first > 0

    # Second call inside the back-off window must not touch the network at all.
    assert await wp._discover_authorization_server(wp.PREVIEW_PROFILE.url) is None
    assert len(attempts) == after_first

    # Once the window lapses, discovery is attempted again.
    wp._ASM_FAILED_UNTIL[wp.PREVIEW_PROFILE.url] = 0.0
    assert await wp._discover_authorization_server(wp.PREVIEW_PROFILE.url) is None
    assert len(attempts) > after_first
