"""Microsoft Agent 365 hosted MCP servers (``workiq-teams`` / ``workiq-user``).

Agent 365 exposes per-tenant, OAuth-protected streamable-HTTP MCP endpoints::

    https://agent365.svc.cloud.microsoft/agents/tenants/{tenant}/servers/{server}

``mcp_TeamsServer`` covers Microsoft Teams (chats, channels, messages, presence)
and ``mcp_MeServer`` covers directory/people lookups. Both sit behind Entra with
RFC 9728 protected-resource metadata pointing at
``login.microsoftonline.com/organizations/v2.0``, so we reuse the whole browser
sign-in stack in :mod:`workiq_preview` via a :class:`WorkIQOAuthProfile` — each
server gets its own loopback port and its own token keys so a Teams sign-in
never clobbers the WorkIQ preview session (or vice versa).

Two wrinkles drive the design:

* **The tenant must be a GUID.** Entra rejects the ``common`` / ``organizations``
  aliases in that URL segment (400 ``TenantIdInvalid``), so we can't ship a
  tenant-agnostic URL. It resolves from the Settings field, then
  ``PRECURSOR_WORKIQ_TENANT_ID``, then — as a convenience — the ``tid`` claim of
  the WorkIQ preview token the user has already signed in with.
* **Entra offers no dynamic client registration**, so the OAuth client id is a
  static public (PKCE, no secret) client rather than something we register.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

from precursor.backend.db import SessionLocal
from precursor.backend.services.app_settings import resolve_workiq_tenant_id
from precursor.backend.services.mcp.workiq_preview import (
    OAUTH_LOGIN_HINT_KEY,
    DbTokenStorage,
    WorkIQOAuthProfile,
    _tenant_from_access_token,
    build_oauth_provider,
)

logger = logging.getLogger(__name__)

AGENT365_BASE_URL: Final = "https://agent365.svc.cloud.microsoft/agents/tenants"
# Public (PKCE, secretless) Entra client registered for Agent 365 MCP access.
AGENT365_CLIENT_ID: Final = "aebc6443-996d-45c2-90f0-388ff96faa56"

_GUID_RE: Final = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_PLACEHOLDER_TENANT: Final = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True, slots=True)
class _Agent365Spec:
    """Static description of one Agent 365 server, minus the tenant."""

    name: str
    label: str
    server_id: str
    redirect_port: int


# Loopback ports registered for the Agent 365 OAuth client, one per server —
# Entra validates a public client's redirect URI *including* the port, so these
# aren't ours to choose: they mirror what the Copilot CLI registers
# (``mcp_TeamsServer`` → 54112, ``mcp_MeServer`` → 54114; each server reserves a
# http/https pair, hence the gap). Being distinct also keeps two sign-ins from
# contending for the same socket.
AGENT365_SERVERS: Final[tuple[_Agent365Spec, ...]] = (
    _Agent365Spec("workiq-teams", "WorkIQ Teams", "mcp_TeamsServer", 54112),
    _Agent365Spec("workiq-user", "WorkIQ User", "mcp_MeServer", 54114),
)

_SPECS_BY_NAME: Final = {spec.name: spec for spec in AGENT365_SERVERS}


def is_agent365_server(name: str) -> bool:
    """Whether ``name`` is one of the built-in Agent 365 MCP servers."""
    return name in _SPECS_BY_NAME


def server_url(spec_or_name: _Agent365Spec | str, tenant_id: str) -> str:
    """Build the per-tenant endpoint URL for an Agent 365 server."""
    spec = _SPECS_BY_NAME[spec_or_name] if isinstance(spec_or_name, str) else spec_or_name
    return f"{AGENT365_BASE_URL}/{tenant_id}/servers/{spec.server_id}"


def build_profile(name: str, tenant_id: str) -> WorkIQOAuthProfile:
    """Build the OAuth profile for one Agent 365 server in a given tenant."""
    spec = _SPECS_BY_NAME[name]
    return WorkIQOAuthProfile(
        server=spec.name,
        label=spec.label,
        url=server_url(spec, tenant_id),
        client_id=AGENT365_CLIENT_ID,
        redirect_port=spec.redirect_port,
        client_name=f"Precursor ({spec.label})",
        tokens_key=f"{spec.name.replace('-', '_')}_oauth_tokens",
        issued_at_key=f"{spec.name.replace('-', '_')}_oauth_issued_at",
        # Tokens stay isolated per server, but the login hint is shared with the
        # rest of the WorkIQ family: it's the same Microsoft identity, and a
        # profile signing in with *no* hint makes Entra reject a silent pass with
        # AADSTS16000 ("multiple user identities are available") instead of
        # redirecting back.
        login_hint_key=OAUTH_LOGIN_HINT_KEY,
        # The Agent 365 client is registered with a bare ``http://127.0.0.1/``
        # loopback (Entra ignores the port), unlike the WorkIQ preview client's
        # ``http://localhost:<port>/callback``.
        redirect_host="127.0.0.1",
        redirect_path="/",
    )


def _valid_tenant(value: str | None) -> str:
    """Return ``value`` when it looks like a tenant GUID, else ``""``."""
    candidate = (value or "").strip()
    return candidate if _GUID_RE.match(candidate) else ""


async def resolve_tenant_id() -> str:
    """Resolve the Entra tenant GUID for the Agent 365 endpoints, or ``""``.

    Settings (DB) → ``PRECURSOR_WORKIQ_TENANT_ID`` → the ``tid`` claim of the
    stored WorkIQ preview access token. The last hop means a user who already
    signed in to hosted WorkIQ gets Teams/User working with no extra config.
    """
    async with SessionLocal() as session:
        configured = _valid_tenant(await resolve_workiq_tenant_id(session))
    if configured:
        return configured
    return _valid_tenant(await discover_tenant_id())


async def discover_tenant_id() -> str:
    """Best-effort tenant GUID from a stored WorkIQ-family access token.

    Checks the WorkIQ preview tokens first, then any Agent 365 tokens already
    obtained (so a re-resolve after sign-in stays stable even if the Settings
    field is cleared).
    """
    storages = [DbTokenStorage()]
    for spec in AGENT365_SERVERS:
        # The tenant only shapes the URL, and we're reading tokens back by
        # AppSetting key here, so a placeholder keeps this side-effect free.
        storages.append(DbTokenStorage(build_profile(spec.name, _PLACEHOLDER_TENANT)))
    for storage in storages:
        try:
            tokens = await storage.get_tokens()
        except Exception:  # pragma: no cover - defensive; storage is DB-backed
            continue
        if tokens is None:
            continue
        tenant = _valid_tenant(_tenant_from_access_token(tokens.access_token))
        if tenant:
            return tenant
    return ""


async def profile_for(name: str) -> WorkIQOAuthProfile | None:
    """Resolve the live OAuth profile for an Agent 365 server, or ``None``.

    ``None`` means no tenant could be resolved yet, so the server can't be
    addressed at all — callers surface that as a configuration error rather than
    attempting a sign-in against an unusable URL.
    """
    if name not in _SPECS_BY_NAME:
        return None
    tenant_id = await resolve_tenant_id()
    if not tenant_id:
        return None
    return build_profile(name, tenant_id)


TENANT_REQUIRED_MESSAGE: Final = (
    "No Microsoft tenant is configured for the Agent 365 servers. Set the tenant "
    "ID in Settings → MCP, or sign in to the hosted WorkIQ preview first so "
    "Precursor can discover it."
)


async def configure_agent365_servers() -> None:
    """Point the built-in Agent 365 entries at the resolved tenant.

    Called on startup and whenever the tenant setting changes. When no tenant can
    be resolved the entries are left disabled with a clear error rather than
    silently pointing at an unusable URL.
    """
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    manager = get_mcp_client_manager()
    tenant_id = await resolve_tenant_id()
    for spec in AGENT365_SERVERS:
        if not tenant_id:
            manager.configure_agent365(spec.name, url=None, auth_provider=None)
            continue
        profile = build_profile(spec.name, tenant_id)
        manager.configure_agent365(
            spec.name,
            url=profile.url,
            auth_provider=build_oauth_provider(profile=profile, interactive=False),
        )
    if tenant_id:
        logger.info("Agent 365 MCP servers configured for tenant %s", tenant_id)
