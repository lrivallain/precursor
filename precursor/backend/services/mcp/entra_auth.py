"""Entra ID sign-in for user-defined MCP servers, through the Azure CLI.

Entra does not support the dynamic client registration the MCP SDK's own OAuth
flow relies on, so a server protected by Entra needs a client it already
trusts. The Azure CLI is one that many organisation APIs pre-authorize, and the
user is usually signed in to it already: ``az account get-access-token`` then
gives a delegated token with no extra prompt.

:func:`detect_server_auth` reads the server's RFC 9728 protected-resource
metadata to fill in the tenant and resource for the user, and
:class:`AzureCliBearerAuth` attaches a fresh token to every request, so a
long-lived pooled session keeps working after the first token expires.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from typing import Final

import httpx2
from mcp.client.auth.utils import (
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    extract_resource_metadata_from_www_auth,
    handle_protected_resource_response,
)
from mcp.shared.auth import ProtectedResourceMetadata

from precursor.backend.schemas.mcp import MCPAuthDetection
from precursor.backend.services.azure_cli import AzureCliError, AzureCliTokenProvider

logger = logging.getLogger(__name__)

_DETECT_TIMEOUT_SECONDS: Final = 10.0
# ``login.microsoftonline.com/<tenant>[/v2.0]`` (any sovereign cloud) or the
# v1 issuer ``sts.windows.net/<tenant>/``.
_ENTRA_AUTHORITY_RE: Final = re.compile(
    r"^https://(?:login\.microsoftonline\.[a-z.]+|login\.partner\.microsoftonline\.cn"
    r"|sts\.windows\.net)/([^/]+)(?:/v2\.0)?/?$",
    re.IGNORECASE,
)
_MULTI_TENANT: Final = frozenset({"common", "organizations", "consumers"})
_AADSTS_RE: Final = re.compile(r"AADSTS(\d+)")
# AADSTS codes that mean "this tenant can't get a token for that resource".
_WRONG_TENANT_CODES: Final = frozenset({"7000118", "500011", "700016", "90072"})
_NO_ACCESS_CODES: Final = frozenset({"65001", "650057", "50105", "500014"})
_BLOCKED_CODES: Final = frozenset({"53000", "53001", "53003", "530003"})
_INITIALIZE_PROBE: Final = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "precursor-auth-probe", "version": "0"},
    },
}


class MCPAzureCliError(AzureCliError):
    """The Azure CLI could not get a token for an MCP server; the message says what to do."""


def login_command(tenant: str, resource: str) -> str:
    """The ``az login`` that grants a token for ``resource`` in ``tenant``."""
    where = f" --tenant {tenant}" if tenant else ""
    return f"az login{where} --scope {resource.rstrip('/')}/.default"


def azure_cli_failure_message(stderr: str, *, tenant: str, resource: str) -> str:
    """Turn ``az account get-access-token`` stderr into the next step for the user."""
    match = _AADSTS_RE.search(stderr)
    code = match.group(1) if match else ""
    suffix = f" (AADSTS{code})" if code else ""
    if code in _WRONG_TENANT_CODES:
        where = f"tenant {tenant}" if tenant else "your Azure CLI's default tenant"
        return (
            f"{resource} doesn't accept tokens from {where}. Set the tenant this server "
            f"expects (use Detect in the server's settings), then reload.{suffix}"
        )
    if code in _NO_ACCESS_CODES:
        return (
            f"Your account isn't allowed to use {resource}. Ask the server's owners for "
            f"access; signing in again won't help.{suffix}"
        )
    if code in _BLOCKED_CODES:
        return (
            "Sign-in was blocked by your organisation's Conditional Access policy. "
            f"Run `{login_command(tenant, resource)}` on a compliant device.{suffix}"
        )
    return (
        f"Azure CLI couldn't get a token for {resource}. Run "
        f"`{login_command(tenant, resource)}`, then reload this server.{suffix}"
    )


class MCPAzureCliTokenProvider(AzureCliTokenProvider):
    error = MCPAzureCliError

    def _missing_cli_message(self) -> str:
        return (
            "Azure CLI (`az`) not found. Install it, run "
            f"`{login_command(self.tenant, self.resource)}`, then reload this server."
        )

    def _failure_message(self, stderr: str) -> str:
        return azure_cli_failure_message(stderr, tenant=self.tenant, resource=self.resource)


# Servers that share a tenant and resource share one cached token.
_PROVIDERS: dict[tuple[str, str], MCPAzureCliTokenProvider] = {}


def token_provider(tenant: str, resource: str) -> MCPAzureCliTokenProvider:
    key = (tenant.strip(), resource.strip())
    provider = _PROVIDERS.get(key)
    if provider is None:
        provider = _PROVIDERS[key] = MCPAzureCliTokenProvider(*key)
    return provider


class AzureCliBearerAuth(httpx2.Auth):
    """Adds ``Authorization: Bearer <token>`` from the Azure CLI to each request."""

    def __init__(self, provider: MCPAzureCliTokenProvider) -> None:
        self._provider = provider

    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response]:
        raise RuntimeError("AzureCliBearerAuth only supports async clients")

    async def bearer_header(self) -> tuple[dict[str, str], datetime] | None:
        """A static ``Authorization`` header and its expiry, for agent sessions.

        The agent runtime only takes fixed headers, so it gets a minted token and
        rebuilds its session before the expiry. ``None`` (logged) when the CLI
        can't supply one, so the caller skips the server.
        """
        try:
            token = await self._provider()
        except MCPAzureCliError as exc:
            logger.warning("Azure CLI token unavailable for an MCP server: %s", exc)
            return None
        expires_at = datetime.fromtimestamp(self._provider.expires_at, UTC)
        return {"Authorization": f"Bearer {token}"}, expires_at

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = f"Bearer {await self._provider()}"
        yield request


def build_auth(auth: str, tenant: str | None, resource: str | None) -> httpx2.Auth | None:
    """The transport auth for a stored server, or None when it uses static headers only."""
    if auth != "azure_cli" or not resource:
        return None
    return AzureCliBearerAuth(token_provider(tenant or "", resource))


def entra_tenant(authorization_server: str) -> str | None:
    """Tenant of an Entra authority URL; "" for multi-tenant ones, None if not Entra."""
    match = _ENTRA_AUTHORITY_RE.match(authorization_server)
    if match is None:
        return None
    tenant = match.group(1)
    return "" if tenant.lower() in _MULTI_TENANT else tenant


def resource_from_scopes(scopes: list[str] | None) -> str | None:
    """Application ID URI from an advertised scope (``api://x/user_impersonation`` -> ``api://x``)."""
    for scope in scopes or []:
        if "://" not in scope:
            continue
        base, _, _name = scope.rstrip("/").rpartition("/")
        if base and not base.endswith(":/"):
            return base
    return None


def detection_from_metadata(metadata: ProtectedResourceMetadata) -> MCPAuthDetection:
    servers = [str(s) for s in metadata.authorization_servers]
    tenant = next((t for t in map(entra_tenant, servers) if t is not None), None)
    if tenant is None:
        return MCPAuthDetection(
            auth_required=True,
            auth="none",
            message=(
                f"This server signs in with {servers[0]}, which isn't Entra ID. "
                "Add an Authorization header instead."
            ),
        )
    resource = resource_from_scopes(metadata.scopes_supported)
    where = f"tenant {tenant}" if tenant else "your organisation's tenant"
    found = f"resource {resource}" if resource else "no resource (enter its application ID URI)"
    return MCPAuthDetection(
        auth_required=True,
        auth="azure_cli",
        tenant=tenant,
        resource=resource,
        message=f"Entra ID sign-in detected: {where}, {found}.",
    )


async def _read_metadata(
    client: httpx2.AsyncClient, url: str, www_auth_url: str | None
) -> ProtectedResourceMetadata | None:
    for candidate in build_protected_resource_metadata_discovery_urls(www_auth_url, url):
        metadata = await handle_protected_resource_response(
            await client.send(create_oauth_metadata_request(candidate))
        )
        if metadata is not None:
            return metadata
    return None


async def detect_server_auth(url: str) -> MCPAuthDetection:
    """Ask ``url`` which sign-in it expects, without sending any credentials."""
    url = url.strip()
    try:
        async with httpx2.AsyncClient(
            timeout=_DETECT_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            async with client.stream(
                "POST",
                url,
                json=_INITIALIZE_PROBE,
                headers={"Accept": "application/json, text/event-stream"},
            ) as response:
                status = response.status_code
                www_auth_url = extract_resource_metadata_from_www_auth(response)
            if status not in (401, 403):
                return MCPAuthDetection(
                    auth_required=False,
                    auth="none",
                    message=f"The server answered without a sign-in (HTTP {status}).",
                )
            metadata = await _read_metadata(client, url, www_auth_url)
    except (httpx2.HTTPError, ValueError) as exc:
        logger.debug("MCP auth detection failed for %s", url, exc_info=True)
        return MCPAuthDetection(
            auth_required=False, auth="none", message=f"Couldn't reach the server: {exc}"
        )
    if metadata is None:
        return MCPAuthDetection(
            auth_required=True,
            auth="none",
            message=(
                f"The server requires a sign-in (HTTP {status}) but doesn't say which one. "
                "Add an Authorization header, or pick Entra ID and fill in the resource."
            ),
        )
    return detection_from_metadata(metadata)
