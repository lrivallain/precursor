"""MCP server configuration for agent SDK sessions.

Owns how a live session's tool servers are assembled: the first-party
``precursor`` stdio entry, the enabled catalog servers (with OAuth bearers baked
into static headers), the fingerprints that decide when a session must be
rebuilt, and the sign-in prompts for servers that couldn't be attached.
``AgentManager`` delegates here; this module must not import ``manager`` at
runtime.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentSession, AppSetting
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents import runtime
from precursor.backend.services.agents.mcp_scope import (
    _MCP_OFF_FINGERPRINT,
    _PRECURSOR_SERVER,
    parse_mcp_scope,
    scope_includes_precursor,
)

if TYPE_CHECKING:
    from precursor.backend.services.agents.live_session import _Caps, _LiveSession
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Long-lived agent SDK sessions bake an OAuth bearer header in at create time
# (the SDK can't refresh a static header). We rebuild the session a little before
# the token actually expires so a transparent re-mint never races a live call.
_OAUTH_REFRESH_MARGIN = timedelta(minutes=5)

# Conservative time-to-live when a token's real expiry can't be determined
# (legacy token saved before we stamped issue time, or no ``expires_in``).
_OAUTH_FALLBACK_TTL = timedelta(minutes=30)


class MCPConfigBuilder:
    def __init__(self, manager: AgentManager) -> None:
        # Held as a back-reference and read at call time, never captured as
        # bound methods, so a test patching the manager still takes effect.
        self._manager = manager

    def precursor_mcp_config(self, agent: AgentSession) -> dict[str, Any] | None:
        """Translate the built-in 'precursor' MCP entry into an SDK stdio config.

        Attaching it lets the agent read topic context and post results back via
        the existing ``post_message`` tool (subject to the user's mcp_expose
        toggles). Returns ``None`` if the SDK isn't loadable.
        """
        try:
            sdk = runtime.load_sdk()
        except RuntimeError:
            return None
        # Reuse the same launcher the in-app MCP client uses, so there's one
        # definition of how to run the precursor server.
        env = dict(os.environ)
        # First-party access: agents bypass the external mcp_expose toggles so
        # they can read topic content and post results back.
        env["PRECURSOR_MCP_FULL_ACCESS"] = "1"
        # Identity, so the ``state_*`` tools can default to *this* agent's
        # scratchpad. Without it the agent would have to discover its own row id
        # before it could save a cursor for its next run.
        env["PRECURSOR_AGENT_ID"] = str(agent.id)
        config: Any = sdk.MCPStdioServerConfig(
            type="stdio",
            command=sys.executable,
            args=["-m", "precursor.backend.services.mcp.precursor_server"],
            env=env,
            # Expose all precursor tools — without this the runtime includes none
            # ([] is the default), so the agent can't read/post topic content.
            tools=["*"],
        )
        return {"precursor": config}

    async def enabled_catalog_fingerprint(
        self, scope: frozenset[str] | None = None
    ) -> frozenset[str]:
        """Names of catalog MCP servers currently enabled *and* registered.

        Excludes ``precursor`` (always attached with full access). Computed the
        same way on both sides of the comparison in :meth:`_ensure_live`, so it
        deliberately reflects the user's toggles rather than which servers
        actually attached — an OAuth server skipped for missing credentials must
        not read as a change and trigger an endless rebuild loop.

        ``scope`` narrows it to a per-agent allowlist (see :func:`parse_mcp_scope`)
        so that re-pointing a shared agent at a differently-scoped workflow step
        reads as a change and rebuilds the session.
        """
        from precursor.backend.services.app_settings import resolve_mcp_enabled
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        async with SessionLocal() as session:
            enabled = await resolve_mcp_enabled(session)
        registered = {entry.name for entry in get_mcp_client_manager().list_entries()}
        return frozenset(
            name
            for name, on in enabled.items()
            if on
            and name != "precursor"
            and name in registered
            and (scope is None or name in scope)
        )

    async def expected_mcp_fingerprint(self, caps: _Caps) -> frozenset[str]:
        """The fingerprint a session created for ``caps`` right now would carry.

        Comparing a live session against *this* — rather than against the raw
        enabled set — is what makes both halves of the tool configuration
        rebuild-sensitive: flipping ``use_mcp``, and narrowing or widening the
        per-step server scope. It also keeps a tools-off agent stable, which the
        raw comparison did not: its stored fingerprint is the off sentinel, so it
        never matched the enabled set and every dispatch tore down and rebuilt a
        session that was already correct.

        ``precursor`` is folded in here rather than in
        :meth:`_enabled_catalog_fingerprint`, which answers a narrower question
        (what the user's toggles enable). The first-party server ignores those
        toggles but *is* scopable, so a step that only re-points precursor still
        has to read as a change.
        """
        scope = parse_mcp_scope(caps.mcp_servers)
        if not caps.use_mcp or (scope is not None and not scope):
            return _MCP_OFF_FINGERPRINT
        catalog = await self._manager._enabled_catalog_fingerprint(scope)
        if scope_includes_precursor(scope):
            return catalog | {_PRECURSOR_SERVER}
        return catalog

    async def catalog_mcp_configs(
        self,
        scope: frozenset[str] | None = None,
    ) -> tuple[dict[str, Any], datetime | None, list[str]]:
        """SDK configs for every catalog MCP server the user has *enabled*.

        Mirrors the chat/topics surface: both built-in servers (``github``,
        ``fetch``, ``workspace-fs``, …) and user-defined ones are attached when
        their ``mcp_enabled`` toggle is on, so an agent can call the same tools.
        ``precursor`` is excluded here — it's attached separately with full
        access in :meth:`precursor_mcp_config`.

        ``scope``, when given, narrows that to an allowlist of server names (a
        workflow step's ``mcp_servers``); ``None`` attaches everything enabled.

        Returns ``(configs, oauth_expires_at, auth_required)``: ``oauth_expires_at``
        is the soonest expiry across any OAuth-protected server whose bearer token
        we baked into a static header (so the caller can refresh before it lapses,
        ``None`` when nothing attached needs it); ``auth_required`` lists enabled
        OAuth servers we *skipped* because no valid credentials are available, so
        the caller can surface an interactive sign-in prompt instead of leaving
        the agent to discover the tools are silently missing. Returns
        ``({}, None, [])`` if the SDK isn't loadable.
        """
        try:
            sdk = runtime.load_sdk()
        except RuntimeError:
            return {}, None, []

        # Imported lazily to keep this module importable without the MCP service
        # graph in the import path of the agents-unavailable case.
        from precursor.backend.services.app_settings import resolve_mcp_enabled
        from precursor.backend.services.github_auth import resolve_github_token
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        async with SessionLocal() as session:
            enabled = await resolve_mcp_enabled(session)
            github_token = await resolve_github_token(session)

        manager = get_mcp_client_manager()
        configs: dict[str, Any] = {}
        oauth_expires_at: datetime | None = None
        auth_required: list[str] = []
        for entry in manager.list_entries():
            # 'precursor' is first-party and attached with full access elsewhere;
            # never gate or duplicate it here.
            if entry.name == "precursor":
                continue
            # Out of the caller's allowlist. Filtered *before* the credential
            # check below so a step scoped away from an OAuth server doesn't
            # raise a sign-in prompt for tools it was never going to use.
            if scope is not None and entry.name not in scope:
                continue
            if not enabled.get(entry.name, False):
                continue
            try:
                config = self._manager._entry_to_sdk_config(sdk, entry, github_token)
            except ValueError as exc:
                logger.warning("Skipping MCP server '%s': %s", entry.name, exc)
                continue
            # OAuth-protected catalog servers (the hosted WorkIQ preview and the
            # Agent 365 pair) authenticate via an httpx.Auth provider that the
            # SDK's static-header HTTP config can't carry. Mint a concrete bearer
            # token and inject it, or skip the server entirely when sign-in is
            # required — attaching it without credentials would just surface 401s
            # as missing tools to the agent.
            if entry.transport == "streamable_http" and entry.auth_provider is not None:
                bearer = await self._manager._oauth_bearer_header(entry.name)
                if bearer is None:
                    logger.warning(
                        "Skipping MCP server '%s' for agent: no valid credentials "
                        "(surfacing an in-app sign-in prompt)",
                        entry.name,
                    )
                    auth_required.append(entry.name)
                    continue
                header, expires_at = bearer
                # Unknown lifetime → assume a conservative TTL so we still rebuild
                # the session periodically rather than letting a stale header rot.
                if expires_at is None:
                    expires_at = datetime.now(UTC) + _OAUTH_FALLBACK_TTL
                oauth_expires_at = (
                    expires_at if oauth_expires_at is None else min(oauth_expires_at, expires_at)
                )
                existing = dict(config.get("headers") or {})
                existing.update(header)
                config["headers"] = existing
            configs[entry.name] = config
        return configs, oauth_expires_at, auth_required

    async def announce_auth_required(self, agent_id: int, servers: list[str]) -> None:
        """Surface a sign-in prompt for each ``server`` we couldn't authenticate.

        Collapsed per credential first: the Agent 365 servers share one Entra
        token, so announcing both would raise two prompts the user can only
        answer once. De-duped per agent on top of that, so a held session
        doesn't re-announce on every rebuild. Servers that are *not* currently
        blocked are dropped from the announced set, so a later token expiry (or
        a sign-in that's since lapsed) prompts again rather than staying silent.
        """
        from precursor.backend.services.mcp.oauth_registry import (
            collapse_by_credential,
            server_label,
        )

        pending = collapse_by_credential(servers)
        announced = self._manager._auth_announced.setdefault(agent_id, set())
        for server in pending:
            if server in announced:
                continue
            announced.add(server)
            label = server_label(server)
            await self._manager._emit_synthetic(
                agent_id,
                AgentEvent(
                    kind="mcp_auth_required",
                    tool_name=server,
                    text=f"{label} needs you to sign in to use its tools.",
                    data={"server": server},
                ),
            )
        # Reset servers that authenticated this build so a future lapse re-fires.
        announced.intersection_update(pending)


def entry_to_sdk_config(sdk: Any, entry: Any, github_token: str) -> Any:
    """Translate one ``MCPServerEntry`` into an SDK MCP server config.

    Raises ``ValueError`` for entries the SDK can't represent (missing
    command/url, unknown transport) so the caller can skip + log them.
    """
    if entry.transport == "stdio":
        if not entry.command:
            raise ValueError("stdio server has no command")
        return sdk.MCPStdioServerConfig(
            type="stdio",
            command=entry.command,
            args=list(entry.args),
            # Built-ins set their own env (or None → inherit ours so PATH and
            # the venv resolve); user entries always inherit ours.
            env=entry.env if entry.env is not None else dict(os.environ),
            tools=["*"],
        )
    if entry.transport == "streamable_http":
        if not entry.url:
            raise ValueError("streamable_http server has no url")
        # headers_provider folds in per-request secrets — the GitHub bearer
        # token for the built-in 'github' server, or a user entry's stored
        # headers. Resolved here, never persisted in agent events.
        headers = entry.headers_provider(github_token) if entry.headers_provider else None
        return sdk.MCPHTTPServerConfig(
            type="http",
            url=entry.url,
            headers=headers or None,
            tools=["*"],
        )
    raise ValueError(f"unsupported transport {entry.transport!r}")


async def oauth_bearer_header(name: str) -> tuple[dict[str, str], datetime | None] | None:
    """Resolve a static ``Authorization`` header for an OAuth catalog server.

    Works for every server Precursor can sign in to — the hosted WorkIQ
    preview *and* the Agent 365 pair — by resolving the server's credential
    profile and minting a bearer from it. Returns ``None`` when the server
    has no usable credential (not an OAuth server, preview mode off, no
    tenant resolved, or no valid token) so the caller skips attaching it
    rather than handing the agent an unauthenticated endpoint. On success
    returns ``(header, expires_at)`` where ``expires_at`` may be ``None`` if
    the token's lifetime can't be determined.
    """
    from precursor.backend.services.mcp.oauth_registry import profile_for_server
    from precursor.backend.services.mcp.workiq_preview import resolve_workiq_bearer_token

    profile = await profile_for_server(name)
    if profile is None:
        return None
    resolved = await resolve_workiq_bearer_token(profile, caller="agent attach")
    if resolved is None:
        return None
    token, expires_at = resolved
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}, expires_at


async def auth_skipped_stamps(servers: list[str]) -> frozenset[tuple[str, str]]:
    """Stamp each of ``servers`` with the credential it would sign in with.

    Answers "has anything changed about the sign-in for the servers we had to
    skip?" cheaply enough to run on every dispatch: it reads the stored
    credential rows straight from the DB and does no network I/O, no bearer
    minting and no profile/tenant resolution — unlike
    :meth:`_oauth_bearer_header`, which drives a real token refresh.

    The stamp is a digest of the stored credential (empty string when the row
    is absent), never the credential itself, so no token material is retained
    in the manager's memory. Servers sharing one credential — the Agent 365
    pair — naturally stamp identically, since
    :func:`~precursor.backend.services.mcp.oauth_registry.credential_key`
    resolves both to the same row.
    """
    if not servers:
        return frozenset()

    from precursor.backend.services.mcp.oauth_registry import credential_key

    keys = {credential_key(name) for name in servers}
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(AppSetting).where(AppSetting.key.in_(keys))))
            .scalars()
            .all()
        )
    values = {row.key: row.value or "" for row in rows}
    return frozenset(
        (name, hashlib.sha256(values.get(credential_key(name), "").encode()).hexdigest())
        for name in servers
    )


def oauth_stale(live: _LiveSession) -> bool:
    """True when ``live``'s baked-in OAuth token is at/within the refresh margin."""
    expires_at = live.oauth_expires_at
    if expires_at is None:
        return False
    return datetime.now(UTC) >= expires_at - _OAUTH_REFRESH_MARGIN


async def auth_server_from_failed_tool(event: AgentEvent) -> str | None:
    """Return the OAuth server to prompt for when a tool failure looks like
    an expired sign-in, else ``None``.

    We require the event to name a server Precursor can actually sign in to
    *and* the bearer to be genuinely unavailable, so a routine tool error
    (bad args, server-side fault) never nags the user to re-auth. Servers
    that can't sign in as things stand resolve to no profile and are
    ignored — notably ``workiq`` with preview mode off, which runs as local
    stdio with no OAuth, so a routine stdio tool error must not surface a
    prompt the user can't act on (re-auth 400s with "Enable WorkIQ preview
    mode before signing in").
    """
    if event.tool_status != "error":
        return None
    server = (event.data or {}).get("server_name")
    if not isinstance(server, str) or not server:
        return None
    from precursor.backend.services.mcp.oauth_registry import profile_for_server
    from precursor.backend.services.mcp.workiq_preview import resolve_workiq_bearer_token

    profile = await profile_for_server(server)
    if profile is None:
        return None
    if await resolve_workiq_bearer_token(profile, caller="agent tool failure") is not None:
        return None
    return server


def blocked_on_missing_auth(agent: AgentSession, live: _LiveSession) -> list[str]:
    """Servers this agent *explicitly* asked for but couldn't authenticate.

    Restricted to an explicit allowlist on purpose. An operator who named a
    server in a workflow step's ``mcp_servers`` stated a hard requirement:
    running the step without it produces a confident answer improvised from
    the model's own knowledge, which the workflow then records as a success.
    An agent left on the whole enabled catalogue made no such claim, and
    hard-blocking it on one lapsed credential would be a regression.

    Returns human-facing labels, collapsed per credential so a pair sharing
    one sign-in reads as one thing to fix.
    """
    scope = parse_mcp_scope(agent.mcp_servers)
    if not scope:
        return []
    from precursor.backend.services.mcp.oauth_registry import (
        collapse_by_credential,
        server_label,
    )

    missing = [name for name, _ in live.mcp_auth_skipped if name in scope]
    return [server_label(name) for name in collapse_by_credential(sorted(missing))]
