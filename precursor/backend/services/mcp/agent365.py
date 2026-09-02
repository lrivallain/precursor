"""Microsoft Agent 365 hosted MCP servers (the ``workiq-*`` family).

Agent 365 exposes per-tenant, OAuth-protected streamable-HTTP MCP endpoints::

    https://agent365.svc.cloud.microsoft/agents/tenants/{tenant}/servers/{server}

Precursor ships the five that one credential reaches: ``mcp_TeamsServer`` (Teams
chats, channels, messages, presence), ``mcp_MeServer`` (directory/people
lookups), ``mcp_PlannerServer`` (plans, tasks and goals) and ``mcp_WordServer`` /
``mcp_ExcelServer`` (create a document, read its content, comment on it). All sit
behind Entra with RFC 9728 protected-resource metadata pointing at
``login.microsoftonline.com/organizations/v2.0``, so we reuse the whole browser
sign-in stack in :mod:`workiq_preview` via a :class:`WorkIQOAuthProfile`.

They **share one credential**: they authenticate as the same Entra client against
the same resource, and the consented scope set spans every ``McpServers.*``
permission — a token minted for one is accepted verbatim by the others. So one
sign-in covers all five, while the WorkIQ preview session (a different client
*and* a different resource) keeps its own separate tokens.

**Not every Agent 365 endpoint is in that audience.** ``mcp_ProductivityServer``,
``mcp_MailServer``, ``mcp_FilesServer``, ``mcp_SharePointServer`` and friends
reject the shared token with ``invalid_audience`` — they are a second Entra
resource, so adding one would mean a second credential and a second sign-in
rather than a new entry below. Their ground is covered by the hosted ``workiq``
preview server anyway, which reaches mail, calendar and files over Graph paths.

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
from precursor.backend.models import AppSetting
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


# Fixed loopback ports, one per server, so two sign-ins never contend for the
# same socket (12798 belongs to the WorkIQ preview flow). Contention is now rare
# — the family shares one credential, so only one of them ever signs in — but the
# ports stay distinct as a cheap guard. The Agent 365 client is registered as a
# public client with the loopback redirect ``http://localhost``, for which Entra
# ignores the port — but *not* the host or the path, so the redirect URI below
# must stay ``localhost`` (never ``127.0.0.1``) with an empty ``/`` path.
# Verified against Entra: ``http://localhost:12799/`` is issued a code, while
# ``http://localhost:12799/callback`` and ``http://127.0.0.1:…/`` both fail with
# AADSTS50011.
AGENT365_SERVERS: Final[tuple[_Agent365Spec, ...]] = (
    _Agent365Spec("workiq-teams", "WorkIQ Teams", "mcp_TeamsServer", 12799),
    _Agent365Spec("workiq-user", "WorkIQ User", "mcp_MeServer", 12800),
    _Agent365Spec("workiq-planner", "WorkIQ Planner", "mcp_PlannerServer", 12801),
    _Agent365Spec("workiq-word", "WorkIQ Word", "mcp_WordServer", 12802),
    _Agent365Spec("workiq-excel", "WorkIQ Excel", "mcp_ExcelServer", 12803),
)

_SPECS_BY_NAME: Final = {spec.name: spec for spec in AGENT365_SERVERS}


def is_agent365_server(name: str) -> bool:
    """Whether ``name`` is one of the built-in Agent 365 MCP servers."""
    return name in _SPECS_BY_NAME


def server_url(spec_or_name: _Agent365Spec | str, tenant_id: str) -> str:
    """Build the per-tenant endpoint URL for an Agent 365 server."""
    spec = _SPECS_BY_NAME[spec_or_name] if isinstance(spec_or_name, str) else spec_or_name
    return f"{AGENT365_BASE_URL}/{tenant_id}/servers/{spec.server_id}"


# Every server above authenticates as the same Entra client against the same
# resource, and the granted scope set covers every ``McpServers.*`` permission —
# verified: a token minted for ``mcp_TeamsServer`` is accepted verbatim by
# ``mcp_MeServer``, ``mcp_PlannerServer``, ``mcp_WordServer`` and
# ``mcp_ExcelServer``. So they share one credential: signing in to any of them
# authenticates all of them.
AGENT365_TOKENS_KEY: Final = "agent365_oauth_tokens"
AGENT365_ISSUED_AT_KEY: Final = "agent365_oauth_issued_at"

# Per-server keys used before the shared credential landed; adopted once on
# startup (see :func:`_adopt_legacy_tokens`) so an existing sign-in survives.
_LEGACY_KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("workiq_teams_oauth_tokens", "workiq_teams_oauth_issued_at"),
    ("workiq_user_oauth_tokens", "workiq_user_oauth_issued_at"),
)


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
        # Shared across the pair: one sign-in covers both servers.
        tokens_key=AGENT365_TOKENS_KEY,
        issued_at_key=AGENT365_ISSUED_AT_KEY,
        # The login hint is shared with the rest of the WorkIQ family too: it's
        # the same Microsoft identity, and a profile signing in with *no* hint
        # makes Entra reject a silent pass with AADSTS16000 ("multiple user
        # identities are available") instead of redirecting back.
        login_hint_key=OAUTH_LOGIN_HINT_KEY,
        # The Agent 365 client registers the loopback redirect ``http://localhost``
        # with an empty path. Entra ignores the *port* of a loopback redirect but
        # matches the host and path exactly, so ``127.0.0.1`` or a ``/callback``
        # path (what the WorkIQ preview client uses) is rejected with AADSTS50011.
        redirect_host="localhost",
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
    signed in to hosted WorkIQ gets the Agent 365 servers working with no extra
    config.
    """
    async with SessionLocal() as session:
        configured = _valid_tenant(await resolve_workiq_tenant_id(session))
    if configured:
        return configured
    return _valid_tenant(await discover_tenant_id())


async def discover_tenant_id() -> str:
    """Best-effort tenant GUID from a stored WorkIQ-family access token.

    Checks the WorkIQ preview tokens first, then the shared Agent 365 token (so a
    re-resolve after sign-in stays stable even if the Settings field is cleared).
    """
    storages = [
        DbTokenStorage(),
        # The tenant only shapes the URL, and we're reading tokens back by
        # AppSetting key here, so a placeholder keeps this side-effect free.
        DbTokenStorage(build_profile(AGENT365_SERVERS[0].name, _PLACEHOLDER_TENANT)),
    ]
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


async def _adopt_legacy_tokens() -> None:
    """Migrate a pre-shared-credential sign-in onto the shared token keys.

    Earlier builds stored a token per Agent 365 server. They are interchangeable,
    so promote whichever survives rather than making the user sign in again, then
    drop the stale rows. No-op once the shared keys exist. Best effort: this is a
    convenience, so a DB hiccup must never keep the servers from being configured.
    """
    try:
        await _migrate_legacy_tokens()
    except Exception:  # pragma: no cover - defensive; worst case is a re-sign-in
        logger.debug("Agent 365 legacy token adoption failed", exc_info=True)


async def _migrate_legacy_tokens() -> None:
    async with SessionLocal() as session:
        if await session.get(AppSetting, AGENT365_TOKENS_KEY) is not None:
            legacy = [
                row
                for keys in _LEGACY_KEYS
                for key in keys
                if (row := await session.get(AppSetting, key)) is not None
            ]
            for row in legacy:
                await session.delete(row)
            if legacy:
                await session.commit()
            return

        for tokens_key, issued_at_key in _LEGACY_KEYS:
            tokens_row = await session.get(AppSetting, tokens_key)
            if tokens_row is None or not tokens_row.value or tokens_row.value == "null":
                continue
            session.add(AppSetting(key=AGENT365_TOKENS_KEY, value=tokens_row.value))
            issued_row = await session.get(AppSetting, issued_at_key)
            if issued_row is not None and issued_row.value:
                session.add(AppSetting(key=AGENT365_ISSUED_AT_KEY, value=issued_row.value))
            logger.info("Adopted the %s sign-in as the shared Agent 365 credential.", tokens_key)
            break
        else:
            return

        for keys in _LEGACY_KEYS:
            for key in keys:
                row = await session.get(AppSetting, key)
                if row is not None:
                    await session.delete(row)
        await session.commit()


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

    await _adopt_legacy_tokens()
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
