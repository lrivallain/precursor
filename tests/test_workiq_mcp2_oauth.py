"""WorkIQ / Agent 365 sign-in on MCP 2's OAuth client, against a simulated Entra.

MCP 2 tightened the OAuth client in two ways that break Entra's multi-tenant
authority. Both only show up when the real SDK flow runs, so this drives the real
``OAuthClientProvider`` subclass end to end over an ``httpx2.MockTransport``,
with the loopback callback answered the way a browser would:

- **Issuer check (SEP-2468).** The resource advertises
  ``…/organizations/v2.0``, but Entra's metadata names the templated issuer
  ``…/{tenantid}/v2.0``. An unmodified provider aborts with "issuer mismatch"
  before it ever shows a sign-in page.
- **Forced consent (SEP-2207).** Entra advertises ``offline_access``, so the SDK
  appends ``prompt=consent``. That would override the silent ``prompt=none`` pass
  and put a consent screen (admin-only in many tenants) in front of every
  sign-in.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from mcp.client.auth import OAuthFlowError
from mcp.shared.auth import OAuthMetadata

import precursor.backend.services.mcp.workiq_preview as wp
from precursor.backend.db import init_db

RESOURCE = wp.WORKIQ_PREVIEW_URL
PRM_URL = "https://workiq.svc.cloud.microsoft/.well-known/oauth-protected-resource/mcp"
AUTHORITY = "https://login.microsoftonline.com/organizations/v2.0"
ENTRA = "https://login.microsoftonline.com"
TOKEN_ENDPOINT = f"{ENTRA}/organizations/oauth2/v2.0/token"
TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"

# Verbatim shapes of the live documents (fetched 2026-09-23), trimmed.
_PRM = {
    "resource": RESOURCE,
    "authorization_servers": [AUTHORITY],
    "scopes_supported": ["fdcc1f02-fc51-4226-8753-f668596af7f7/WorkIQAgent.Ask"],
    "bearer_methods_supported": ["header"],
}
_ENTRA_METADATA = {
    "issuer": f"{ENTRA}/{{tenantid}}/v2.0",
    "authorization_endpoint": f"{ENTRA}/organizations/oauth2/v2.0/authorize",
    "token_endpoint": TOKEN_ENDPOINT,
    "response_types_supported": ["code", "id_token", "code id_token", "id_token token"],
    "scopes_supported": ["openid", "profile", "email", "offline_access"],
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _profile() -> wp.WorkIQOAuthProfile:
    # A private port and private storage keys: no collision with a real
    # instance's fixed loopback, no leak into other tests' tokens.
    return replace(
        wp.PREVIEW_PROFILE,
        redirect_port=_free_port(),
        tokens_key="test_mcp2_oauth_tokens",
        issued_at_key="test_mcp2_oauth_issued_at",
        login_hint_key="test_mcp2_oauth_login_hint",
    )


def _entra(requests: list[httpx2.Request]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        url = str(request.url)
        if url == RESOURCE:
            if request.headers.get("authorization") != "Bearer fresh":
                return httpx2.Response(
                    401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{PRM_URL}"'}
                )
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if url == PRM_URL:
            return httpx2.Response(200, json=_PRM)
        if url == f"{AUTHORITY}/.well-known/openid-configuration":
            return httpx2.Response(200, json=_ENTRA_METADATA)
        if url == TOKEN_ENDPOINT:
            return httpx2.Response(
                200,
                json={
                    "access_token": "fresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "renewable",
                },
            )
        # RFC 8414 path-aware discovery URLs Entra doesn't serve.
        return httpx2.Response(404)

    return httpx2.MockTransport(handler)


async def _answer_loopback(port: int, query: dict[str, str]) -> None:
    """Deliver the browser redirect once the loopback is listening."""
    for _ in range(200):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.write(
            f"GET /callback?{urlencode(query)} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
        )
        await writer.drain()
        await reader.read()
        writer.close()
        return
    raise AssertionError("the loopback never started listening")


async def _sign_in(
    monkeypatch: pytest.MonkeyPatch, *, prompt: str | None, iss: str | None = None
) -> tuple[list[str], httpx2.Response]:
    """Run a full interactive sign-in; return the authorization URLs and the retried call."""
    await init_db()
    profile = _profile()
    await wp.clear_workiq_oauth_tokens(profile, reason="test")
    authorization_urls: list[str] = []
    pending: list[asyncio.Task[None]] = []

    async def _no_seed(_url: str) -> None:
        return None

    async def _browser(_server: str, url: str) -> None:
        authorization_urls.append(url)
        query = {"code": "the-code", "state": parse_qs(urlsplit(url).query)["state"][0]}
        if iss is not None:
            query["iss"] = iss
        pending.append(asyncio.create_task(_answer_loopback(profile.redirect_port, query)))

    # Seeding would reach the real network; the 401 branch discovers instead.
    monkeypatch.setattr(wp, "_discover_authorization_server", _no_seed)
    monkeypatch.setattr(wp, "publish_mcp_auth_url", _browser)

    provider = wp.build_oauth_provider(
        profile=profile, interactive=True, open_system_browser=False, prompt=prompt
    )
    requests: list[httpx2.Request] = []
    try:
        async with httpx2.AsyncClient(transport=_entra(requests), auth=provider) as client:
            response = await client.post(RESOURCE, json={"jsonrpc": "2.0", "id": 1})
    finally:
        for task in pending:
            with contextlib.suppress(BaseException):
                await task
        await wp.clear_workiq_oauth_tokens(profile, reason="test")
    return authorization_urls, response


async def test_multi_tenant_sign_in_passes_the_issuer_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls, response = await _sign_in(monkeypatch, prompt=None)

    assert response.status_code == 200
    assert len(urls) == 1
    params = parse_qs(urlsplit(urls[0]).query)
    assert "prompt" not in params  # the SDK's forced consent is gone
    assert "offline_access" in params["scope"][0].split()


async def test_silent_pass_keeps_prompt_none(monkeypatch: pytest.MonkeyPatch) -> None:
    urls, response = await _sign_in(monkeypatch, prompt="none")

    assert response.status_code == 200
    assert parse_qs(urlsplit(urls[0]).query)["prompt"] == ["none"]


async def test_a_tenant_specific_iss_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entra can only name the real tenant, never the ``{tenantid}`` template."""
    _urls, response = await _sign_in(
        monkeypatch, prompt=None, iss=f"https://login.microsoftonline.com/{TENANT}/v2.0"
    )
    assert response.status_code == 200


async def test_a_foreign_iss_is_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only Entra's own tenant issuers are reconciled; RFC 9207 still bites."""
    with pytest.raises(OAuthFlowError, match="iss mismatch"):
        await _sign_in(monkeypatch, prompt=None, iss="https://attacker.example/v2.0")


def test_templated_issuer_matches_what_the_sdk_parses() -> None:
    """The constant must equal the SDK's own rendering of Entra's issuer."""
    parsed = OAuthMetadata.model_validate(_ENTRA_METADATA)
    assert str(parsed.issuer) == wp._ENTRA_TEMPLATED_ISSUER


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        (AUTHORITY, wp._ENTRA_TEMPLATED_ISSUER),
        ("https://login.microsoftonline.com/common/v2.0", wp._ENTRA_TEMPLATED_ISSUER),
        # A tenant-specific authority publishes its concrete issuer: keep it strict.
        (f"https://login.microsoftonline.com/{TENANT}/v2.0", f"{ENTRA}/{TENANT}/v2.0"),
        # Not Entra at all: untouched.
        ("https://as.example.com", "https://as.example.com"),
        ("https://login.microsoftonline.com.evil.example/organizations/v2.0", None),
    ],
)
def test_expected_issuer_mapping(authority: str, expected: str | None) -> None:
    assert wp._entra_expected_issuer(authority) == (expected or authority)


def test_response_iss_reconciliation_is_narrow() -> None:
    templated = OAuthMetadata.model_validate(_ENTRA_METADATA)
    concrete = OAuthMetadata.model_validate({**_ENTRA_METADATA, "issuer": f"{ENTRA}/{TENANT}/v2.0"})
    tenant_iss = f"{ENTRA}/{TENANT}/v2.0"

    assert wp._entra_response_iss(tenant_iss, templated) == wp._ENTRA_TEMPLATED_ISSUER
    assert wp._entra_response_iss(None, templated) is None
    # Not a GUID tenant, a foreign host, or a metadata that isn't templated.
    assert wp._entra_response_iss(f"{ENTRA}/contoso/v2.0", templated) == f"{ENTRA}/contoso/v2.0"
    assert wp._entra_response_iss("https://x.example/v2.0", templated) == "https://x.example/v2.0"
    assert wp._entra_response_iss(tenant_iss, concrete) == tenant_iss


def test_authorization_url_drops_only_the_sdks_consent_prompt() -> None:
    base = "https://login.example/authorize?client_id=abc&scope=x+offline_access"

    forced = wp._augment_authorization_url(f"{base}&prompt=consent", login_hint=None, prompt=None)
    assert "prompt" not in parse_qs(urlsplit(forced).query)

    silent = wp._augment_authorization_url(f"{base}&prompt=consent", login_hint=None, prompt="none")
    assert parse_qs(urlsplit(silent).query)["prompt"] == ["none"]

    # Any other SDK-set prompt still wins, as before.
    login = wp._augment_authorization_url(f"{base}&prompt=login", login_hint=None, prompt="none")
    assert parse_qs(urlsplit(login).query)["prompt"] == ["login"]


async def test_preregistered_client_carries_no_issuer_binding() -> None:
    """MCP 2 binds stored client credentials to an issuer (SEP-2352).

    The WorkIQ client is pre-registered and handed to the SDK fresh each time, so
    it must carry no ``issuer``: otherwise the SDK would discard it and try a
    dynamic registration that Entra doesn't offer.
    """
    info = await wp.DbTokenStorage(_profile()).get_client_info()
    assert info is not None
    assert info.client_id == wp.WORKIQ_OAUTH_CLIENT_ID
    assert info.issuer is None
