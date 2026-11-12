"""Entra ID (Azure CLI) sign-in for user-defined MCP servers."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.shared.auth import ProtectedResourceMetadata

from precursor.backend.main import create_app
from precursor.backend.services.mcp import entra_auth
from precursor.backend.services.mcp.client import get_mcp_client_manager

MS_TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
RESOURCE = "api://04e71488-290c-4fb7-b2fa-42a5d7793f29"
SERVER_URL = "https://gateway.example.com/data/mcp"
METADATA_URL = "https://gateway.example.com/.well-known/oauth-protected-resource/data/mcp"
EOIQ_METADATA = {
    "resource": "https://gateway.example.com",
    "authorization_servers": [f"https://login.microsoftonline.com/{MS_TENANT}/v2.0"],
    "scopes_supported": [f"{RESOURCE}/user_impersonation"],
    "bearer_methods_supported": ["header"],
}


@pytest.mark.parametrize(
    ("authority", "tenant"),
    [
        (f"https://login.microsoftonline.com/{MS_TENANT}/v2.0", MS_TENANT),
        (f"https://sts.windows.net/{MS_TENANT}/", MS_TENANT),
        ("https://login.microsoftonline.com/organizations/v2.0", ""),
        ("https://accounts.google.com", None),
    ],
)
def test_entra_tenant(authority: str, tenant: str | None) -> None:
    assert entra_auth.entra_tenant(authority) == tenant


@pytest.mark.parametrize(
    ("scopes", "resource"),
    [
        ([f"{RESOURCE}/user_impersonation"], RESOURCE),
        (["openid", "https://api.example.com/.default"], "https://api.example.com"),
        (["openid", RESOURCE], None),
        (None, None),
    ],
)
def test_resource_from_scopes(scopes: list[str] | None, resource: str | None) -> None:
    assert entra_auth.resource_from_scopes(scopes) == resource


def test_detection_from_entra_metadata() -> None:
    detection = entra_auth.detection_from_metadata(
        ProtectedResourceMetadata.model_validate(EOIQ_METADATA)
    )
    assert detection.auth == "azure_cli"
    assert detection.tenant == MS_TENANT
    assert detection.resource == RESOURCE


def test_detection_from_non_entra_metadata() -> None:
    metadata = {**EOIQ_METADATA, "authorization_servers": ["https://auth.example.com"]}
    detection = entra_auth.detection_from_metadata(
        ProtectedResourceMetadata.model_validate(metadata)
    )
    assert detection.auth_required
    assert detection.auth == "none"
    assert "isn't Entra ID" in detection.message


def _mock_http(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx2.AsyncClient

    def factory(**kwargs: Any) -> httpx2.AsyncClient:
        return real(transport=httpx2.MockTransport(handler), **kwargs)

    monkeypatch.setattr(entra_auth.httpx2, "AsyncClient", factory)


async def test_detect_server_auth_follows_www_authenticate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == SERVER_URL:
            assert "authorization" not in request.headers
            return httpx2.Response(
                401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{METADATA_URL}"'}
            )
        if str(request.url) == METADATA_URL:
            return httpx2.Response(200, json=EOIQ_METADATA)
        return httpx2.Response(404)

    _mock_http(monkeypatch, handler)
    detection = await entra_auth.detect_server_auth(f" {SERVER_URL} ")
    assert (detection.auth, detection.tenant, detection.resource) == (
        "azure_cli",
        MS_TENANT,
        RESOURCE,
    )


async def test_detect_server_auth_open_server(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_http(monkeypatch, lambda _request: httpx2.Response(200, json={}))
    detection = await entra_auth.detect_server_auth(SERVER_URL)
    assert not detection.auth_required
    assert detection.auth == "none"


async def test_detect_server_auth_without_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_http(monkeypatch, lambda _request: httpx2.Response(401))
    detection = await entra_auth.detect_server_auth(SERVER_URL)
    assert detection.auth_required
    assert detection.auth == "none"
    assert "doesn't say which one" in detection.message


async def test_detect_server_auth_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    _mock_http(monkeypatch, handler)
    detection = await entra_auth.detect_server_auth(SERVER_URL)
    assert detection.message.startswith("Couldn't reach the server")


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("ERROR: AADSTS7000118: Resource application ... not allowed", "doesn't accept tokens"),
        ("ERROR: AADSTS65001: The user or administrator has not consented", "isn't allowed"),
        ("ERROR: AADSTS53003: Access has been blocked by Conditional Access", "Conditional"),
        (
            "ERROR: Please run 'az login' to setup account.",
            f"az login --tenant t1 --scope {RESOURCE}/.default",
        ),
    ],
)
def test_failure_message_gives_the_next_step(stderr: str, expected: str) -> None:
    assert expected in entra_auth.azure_cli_failure_message(stderr, tenant="t1", resource=RESOURCE)


class _StubProvider(entra_auth.MCPAzureCliTokenProvider):
    def __init__(self) -> None:
        super().__init__("t1", RESOURCE)
        self.calls = 0

    async def _fetch(self) -> tuple[str, float]:
        self.calls += 1
        return f"tok{self.calls}", time.time() + 3600


async def test_bearer_auth_adds_cached_cli_token() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["authorization"])
        return httpx2.Response(200)

    provider = _StubProvider()
    auth = entra_auth.AzureCliBearerAuth(provider)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), auth=auth) as client:
        await client.post(SERVER_URL)
        await client.post(SERVER_URL)
    assert seen == ["Bearer tok1", "Bearer tok1"]
    assert provider.calls == 1


def test_build_auth_shares_one_provider_per_tenant_and_resource() -> None:
    assert entra_auth.build_auth("none", MS_TENANT, RESOURCE) is None
    assert entra_auth.build_auth("azure_cli", MS_TENANT, None) is None
    assert entra_auth.token_provider(MS_TENANT, RESOURCE) is entra_auth.token_provider(
        f" {MS_TENANT} ", RESOURCE
    )
    assert isinstance(
        entra_auth.build_auth("azure_cli", MS_TENANT, RESOURCE), entra_auth.AzureCliBearerAuth
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _create(client: TestClient, **fields: Any) -> Any:
    body = {
        "name": f"eoiq-{uuid.uuid4().hex[:8]}",
        "transport": "streamable_http",
        "url": SERVER_URL,
        **fields,
    }
    return client.post("/api/mcp/servers/user", json=body)


def test_create_azure_cli_server_requires_a_resource(client: TestClient) -> None:
    resp = _create(client, auth="azure_cli", auth_tenant=MS_TENANT)
    assert resp.status_code == 400
    assert "resource is required" in resp.text


def test_create_azure_cli_server_wires_the_bearer_auth(client: TestClient) -> None:
    resp = _create(client, auth="azure_cli", auth_tenant=MS_TENANT, auth_resource=f" {RESOURCE} ")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert (data["auth"], data["auth_tenant"], data["auth_resource"]) == (
        "azure_cli",
        MS_TENANT,
        RESOURCE,
    )
    entry = get_mcp_client_manager().get(data["name"])
    assert entry is not None
    assert isinstance(entry.auth_provider, entra_auth.AzureCliBearerAuth)

    listed = {s["name"]: s for s in client.get("/api/mcp/servers?probe=false").json()}
    assert listed[data["name"]]["auth"] == "azure_cli"

    resp = client.patch(f"/api/mcp/servers/user/{data['id']}", json={"auth": "none"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["auth"], resp.json()["auth_resource"]) == ("none", None)
    assert get_mcp_client_manager().get(data["name"]).auth_provider is None  # type: ignore[union-attr]
    client.delete(f"/api/mcp/servers/user/{data['id']}")


def test_invalid_tenant_is_rejected(client: TestClient) -> None:
    resp = _create(client, auth="azure_cli", auth_tenant="--evil", auth_resource=RESOURCE)
    assert resp.status_code == 400


def test_stdio_server_never_uses_azure_cli(client: TestClient) -> None:
    resp = _create(
        client,
        transport="stdio",
        url=None,
        command="echo",
        auth="azure_cli",
        auth_resource=RESOURCE,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["auth"] == "none"
    client.delete(f"/api/mcp/servers/user/{resp.json()['id']}")


def test_detect_auth_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(url: str) -> Any:
        return entra_auth.MCPAuthDetection(
            auth_required=True, auth="azure_cli", tenant=MS_TENANT, resource=RESOURCE, message="ok"
        )

    from precursor.backend.routers import mcp_user_servers

    monkeypatch.setattr(mcp_user_servers, "detect_server_auth", fake)
    resp = client.post("/api/mcp/servers/detect-auth", json={"url": SERVER_URL})
    assert resp.status_code == 200
    assert resp.json()["resource"] == RESOURCE


async def test_bearer_header_for_agent_sessions() -> None:
    auth = entra_auth.AzureCliBearerAuth(_StubProvider())
    resolved = await auth.bearer_header()
    assert resolved is not None
    header, expires_at = resolved
    assert header == {"Authorization": "Bearer tok1"}
    assert expires_at.timestamp() > time.time() + 3000


async def test_bearer_header_is_none_when_the_cli_fails() -> None:
    class _Failing(entra_auth.MCPAzureCliTokenProvider):
        async def _fetch(self) -> tuple[str, float]:
            raise self.error("run az login")

    auth = entra_auth.AzureCliBearerAuth(_Failing("t1", RESOURCE))
    assert await auth.bearer_header() is None


async def test_agent_sessions_get_the_cli_bearer() -> None:
    from precursor.backend.services.agents import mcp_config

    manager = get_mcp_client_manager()
    name = f"eoiq-{uuid.uuid4().hex[:8]}"
    manager.register_user_entry(
        name=name,
        transport="streamable_http",
        url=SERVER_URL,
        auth_provider=entra_auth.AzureCliBearerAuth(_StubProvider()),
    )
    try:
        resolved = await mcp_config.oauth_bearer_header(name)
    finally:
        manager.unregister_user_entry(name)
    assert resolved is not None
    assert resolved[0] == {"Authorization": "Bearer tok1"}
