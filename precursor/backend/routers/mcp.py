"""HTTP-facing MCP endpoints.

Lets the frontend Settings panel list configured servers, toggle them on/off
(persisted in app settings under ``mcp_enabled``), probe a server to refresh
its connection state + tool catalog, and CRUD user-defined entries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import get_session
from precursor.backend.models import AppSetting, MCPServer
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.mcp.client import get_mcp_client_manager
from precursor.backend.services.mcp.server import get_mcp_server
from precursor.backend.services.mcp.user_servers import (
    apply_to_manager,
    get_row_by_name,
    to_public_dict,
)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_RESERVED_NAMES = {"github", "workiq", "fetch", "workspace-fs", "cmd-runner", "precursor"}


async def _load_enabled(session: AsyncSession) -> dict[str, bool]:
    row = await session.get(AppSetting, "mcp_enabled")
    if row is None:
        return {}
    try:
        data = json.loads(row.value)
    except (TypeError, ValueError):
        return {}
    return {k: bool(v) for k, v in data.items()} if isinstance(data, dict) else {}


async def _store_enabled(session: AsyncSession, enabled: dict[str, bool]) -> None:
    row = await session.get(AppSetting, "mcp_enabled")
    encoded = json.dumps(enabled)
    if row is None:
        session.add(AppSetting(key="mcp_enabled", value=encoded))
    else:
        row.value = encoded
    await session.commit()


async def _preflight_block(name: str, session: AsyncSession) -> str | None:
    """Reason ``name`` can't be enabled now, or None. Host-dependency gate."""
    if name == "cmd-runner":
        from precursor.backend.services.app_settings import resolve_cmd_runner_config
        from precursor.backend.services.cmd_runner import jail_preflight_error

        config = await resolve_cmd_runner_config(session)
        return jail_preflight_error(config.jail)
    return None


def _enrich_with_user_meta(base: dict[str, Any], row: MCPServer | None) -> dict[str, Any]:
    """Attach DB-only fields (id, header_keys) to a status dict when user-defined."""
    if row is None:
        return {**base, "id": None, "header_keys": []}
    meta = to_public_dict(row)
    return {
        **base,
        "id": meta["id"],
        "header_keys": meta["header_keys"],
    }


@router.get("/servers")
async def list_servers(
    probe: bool = True,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    manager = get_mcp_client_manager()
    enabled = await _load_enabled(session)
    # Enabled servers with an empty tool catalogue (e.g. after a process
    # restart) need a probe to resolve their real state + tools. The chat
    # router opens its own sessions, but the UI relies on the cached tools
    # list to render the catalogue.
    stale = [
        entry.name
        for entry in manager.list_entries()
        if enabled.get(entry.name, False) and not entry.tools
    ]
    if probe and stale:
        # Probe concurrently so a slow server (stdio spin-up, network, OAuth)
        # doesn't serialise behind the others and stall the whole listing.
        github_token = await resolve_github_token(session)
        await asyncio.gather(*(manager.probe(name, github_token=github_token) for name in stale))

    # When probing is deferred (``probe=false``), the UI wants the list back
    # immediately and resolves each status afterwards via ``/servers/{name}/probe``.
    # Report unresolved enabled servers as "connecting" so each card shows its
    # own spinner instead of the whole list sitting in a loading state.
    pending = set() if probe else set(stale)
    out: list[dict[str, Any]] = []
    for entry in manager.list_entries():
        base = manager.status_dict(entry, enabled=enabled.get(entry.name, False))
        if entry.name in pending:
            base["state"] = "connecting"
        row = None if entry.builtin else await get_row_by_name(session, entry.name)
        out.append(_enrich_with_user_meta(base, row))
    return out


@router.post("/servers/{name}/probe")
async def probe_server(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Probe a single server to resolve its state + tool catalog for the UI.

    Complements the deferred-status listing (``GET /servers?probe=false``): the
    Settings panel renders the server list right away, then calls this per
    server so each card resolves independently and a slow server never blocks
    the others. Unlike ``/refresh`` this does not retire the warm worker — it
    just opens and closes a session to refresh the cached catalogue.
    """
    manager = get_mcp_client_manager()
    entry = manager.get(name)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")

    enabled = await _load_enabled(session)
    if enabled.get(name, False):
        entry = await manager.probe(name, github_token=await resolve_github_token(session))
    base = manager.status_dict(entry, enabled=enabled.get(name, False))
    row = None if entry.builtin else await get_row_by_name(session, name)
    return _enrich_with_user_meta(base, row)


@router.get("/server/info")
async def server_info() -> dict[str, Any]:
    return get_mcp_server().describe()


@router.post("/servers/{name}/connect")
async def connect_server(
    name: str,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enable + probe a server, refreshing its tool catalog for the UI."""
    manager = get_mcp_client_manager()

    # Preflight: refuse to enable a server whose host dependencies are missing.
    # cmd-runner needs Docker when jail mode is on — resolve the *effective*
    # jail setting (env default + DB override) and surface a known error
    # instead of flipping on a broken server.
    block = await _preflight_block(name, session)
    if block is not None:
        enabled = await _load_enabled(session)
        enabled[name] = False
        await _store_enabled(session, enabled)
        entry = manager.get(name)
        if entry is None:
            return {"name": name, "state": "error", "error": block, "enabled": False}
        entry.state = "error"
        entry.error = block
        base = manager.status_dict(entry, enabled=False)
        row = None if entry.builtin else await get_row_by_name(session, name)
        return _enrich_with_user_meta(base, row)

    enabled = await _load_enabled(session)
    enabled[name] = True
    await _store_enabled(session, enabled)

    entry = await manager.probe(name, github_token=await resolve_github_token(session))
    base = manager.status_dict(entry, enabled=True)
    row = None if entry.builtin else await get_row_by_name(session, name)
    return _enrich_with_user_meta(base, row)


@router.post("/servers/{name}/disconnect")
async def disconnect_server(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    manager = get_mcp_client_manager()
    enabled = await _load_enabled(session)
    enabled[name] = False
    await _store_enabled(session, enabled)
    entry = manager.get(name)
    if entry is None:
        return {"name": name, "state": "disabled", "enabled": False}
    base = manager.status_dict(entry, enabled=False)
    row = None if entry.builtin else await get_row_by_name(session, name)
    return _enrich_with_user_meta(base, row)


@router.post("/servers/{name}/refresh")
async def refresh_server(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Re-probe an already-enabled server to refresh its tool catalog.

    Lets the UI reload the tool list without the disable/enable round-trip. We
    retire any warm worker first so the probe spins up a fresh session (and, for
    stdio servers, a fresh process) rather than reusing a stale catalogue.
    """
    manager = get_mcp_client_manager()
    entry = manager.get(name)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")

    enabled = await _load_enabled(session)
    if not enabled.get(name, False):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Enable the server before reloading its tools.",
        )

    await manager.retire_worker(name)
    entry = await manager.probe(name, github_token=await resolve_github_token(session))
    base = manager.status_dict(entry, enabled=True)
    row = None if entry.builtin else await get_row_by_name(session, name)
    return _enrich_with_user_meta(base, row)


class WorkiqPreviewToggle(BaseModel):
    enabled: bool


@router.post("/servers/workiq/preview")
async def set_workiq_preview_mode(
    payload: WorkiqPreviewToggle,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Switch the built-in ``workiq`` server between the local stdio launcher and
    the hosted, OAuth-protected HTTP endpoint (full read+write surface).

    Reconfigures the in-memory entry and retires any warm session so the next
    connection picks up the new transport. We deliberately do **not** probe here:
    the OAuth browser sign-in runs lazily on the next connect (toggling the
    server) or first chat use, so flipping this checkbox never blocks on an
    interactive login.
    """
    from precursor.backend.services.mcp.workiq_preview import (
        build_oauth_provider,
        set_workiq_preview,
    )

    manager = get_mcp_client_manager()
    if manager.get("workiq") is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WorkIQ MCP server not found")

    await set_workiq_preview(payload.enabled)
    manager.configure_workiq_preview(
        payload.enabled,
        auth_provider=build_oauth_provider() if payload.enabled else None,
    )
    await manager.retire_worker("workiq")

    enabled = await _load_enabled(session)
    is_enabled = enabled.get("workiq", False)

    entry = manager.get("workiq")
    assert entry is not None
    base = manager.status_dict(entry, enabled=is_enabled)
    return _enrich_with_user_meta(base, None)


@router.post("/servers/workiq/reauthenticate")
async def reauthenticate_workiq_server(
    use_popup: bool = False,
    silent_only: bool = False,
    auto: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Restart the WorkIQ OAuth sign-in on an explicit user action.

    Background connects never pop a browser (they surface ``needs_auth``); this
    endpoint runs the interactive authorization-code grant, persists the fresh
    tokens, then rebuilds the background provider and re-probes so the next chat
    turn reuses the new session. Blocks until the browser flow completes.

    ``use_popup`` is set by the SPA when it has already opened a script-openable
    popup for the sign-in: we then skip the OS-browser fallback and only surface
    the authorization URL over SSE for that popup to navigate to.

    ``auto`` runs the hands-free **self-triggering** re-auth (gated by
    :attr:`Settings.workiq_auto_reauth_enabled`): the invisible ``prompt=none``
    pass first (the SPA drives it through a hidden iframe) and, when that needs
    interaction, the backend *self-opens the OS browser* to the visible prompt —
    no banner click. Only when even that can't complete does it return the status
    with ``interaction_required=true`` so the SPA falls back to the manual
    "Sign in" banner as a last resort.

    ``silent_only`` runs *only* the invisible ``prompt=none`` pass (no OS-browser
    fallback), returning ``interaction_required=true`` the moment it can't
    complete silently. Retained for callers that want the pure silent probe.
    """
    from precursor.backend.config import get_settings
    from precursor.backend.services.mcp.client import _describe_exception, _find_in_exception
    from precursor.backend.services.mcp.workiq_preview import (
        WorkIQAuthCancelledError,
        WorkIQAuthInProgressError,
        WorkIQAuthPortBusyError,
        build_oauth_provider,
        reauthenticate_workiq,
        resolve_workiq_preview,
    )

    manager = get_mcp_client_manager()
    if manager.get("workiq") is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WorkIQ MCP server not found")
    if not await resolve_workiq_preview():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Enable WorkIQ preview mode before signing in.",
        )

    enabled = await _load_enabled(session)
    is_enabled = enabled.get("workiq", False)

    # Hands-free auto re-auth turned off → don't self-trigger anything; report
    # interaction required so the SPA shows the manual banner straight away.
    if (silent_only or auto) and not get_settings().workiq_auto_reauth_enabled:
        entry = manager.get("workiq")
        assert entry is not None
        base = manager.status_dict(entry, enabled=is_enabled)
        return {**_enrich_with_user_meta(base, None), "interaction_required": True}

    try:
        if silent_only:
            authenticated = await reauthenticate_workiq(silent_only=True)
        elif auto:
            authenticated = await reauthenticate_workiq(auto=True)
        else:
            await reauthenticate_workiq(open_system_browser=not use_popup)
            authenticated = True
    except WorkIQAuthInProgressError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except WorkIQAuthPortBusyError as exc:
        # Another Precursor window (or app) owns the fixed OAuth loopback port —
        # a conflict the user resolves by finishing/closing that sign-in.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except WorkIQAuthCancelledError as exc:
        # The SPA cancelled this sign-in (its popup was closed). Report a benign
        # conflict; the SPA already knows and simply drops its "Signing in…".
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception as exc:
        # A port-busy or user-cancel raised deep in the SDK's auth flow surfaces
        # wrapped in its task-group ``BaseExceptionGroup`` — unwrap so it still
        # reads as a clear conflict rather than an opaque gateway failure.
        conflict = _find_in_exception(exc, WorkIQAuthPortBusyError) or _find_in_exception(
            exc, WorkIQAuthCancelledError
        )
        if conflict is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, str(conflict)) from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"WorkIQ sign-in failed: {_describe_exception(exc)}",
        ) from exc

    # A silent pass that needs a human: leave the warm worker parked in
    # ``needs_auth`` and tell the SPA to surface the manual sign-in banner.
    if not authenticated:
        entry = manager.get("workiq")
        assert entry is not None
        base = manager.status_dict(entry, enabled=is_enabled)
        return {**_enrich_with_user_meta(base, None), "interaction_required": True}

    # Swap in a fresh background (non-interactive) provider so it reads the newly
    # persisted tokens, drop the stale warm worker, then refresh the catalog.
    manager.configure_workiq_preview(True, auth_provider=build_oauth_provider())
    await manager.retire_worker("workiq")

    if is_enabled:
        await manager.probe("workiq", github_token=await resolve_github_token(session))

    entry = manager.get("workiq")
    assert entry is not None
    # Wake any chat turn paused waiting for this sign-in so it resumes with the
    # freshly authenticated tools instead of timing out.
    manager.signal_auth_resolved()
    # Agents bake a static OAuth bearer into their SDK session at creation, so an
    # agent built before sign-in still lacks WorkIQ's tools. Drop idle sessions so
    # the next dispatch rebuilds with the new token (no-op when agents are off).
    try:
        from precursor.backend.services.agents.manager import get_agent_manager

        await get_agent_manager().refresh_oauth_sessions()
    except Exception:  # pragma: no cover - agents runtime is optional
        logger.warning("Could not refresh agent sessions after WorkIQ sign-in", exc_info=True)
    base = manager.status_dict(entry, enabled=is_enabled)
    return _enrich_with_user_meta(base, None)


@router.post("/servers/workiq/reauthenticate/cancel")
async def cancel_reauthenticate_workiq_server() -> dict[str, bool]:
    """Abort an in-flight interactive WorkIQ sign-in and free the loopback port.

    The SPA calls this when its sign-in popup closes without completing, so the
    loopback stops waiting and releases the fixed redirect port immediately —
    otherwise a walked-away flow would squat it (blocking every other Precursor
    window) until the callback times out. A no-op (``cancelled: false``) when no
    interactive sign-in is waiting, or once the redirect has already arrived.
    """
    from precursor.backend.services.mcp.workiq_preview import cancel_reauthenticate_workiq

    return {"cancelled": cancel_reauthenticate_workiq()}


# --------- user-defined CRUD ---------


class UserServerBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    transport: str = Field(pattern=r"^(streamable_http|stdio)$")
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must start with a letter and contain only lowercase letters, "
                "digits, or hyphens (max 64 chars)"
            )
        return v


class UserServerCreate(UserServerBase):
    pass


class UserServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    transport: str | None = Field(default=None, pattern=r"^(streamable_http|stdio)$")
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    headers: dict[str, str] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must start with a letter and contain only lowercase letters, "
                "digits, or hyphens (max 64 chars)"
            )
        return v


def _validate_payload(transport: str, url: str | None, command: str | None) -> None:
    if transport == "streamable_http" and not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url is required for streamable_http")
    if transport == "stdio" and not command:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "command is required for stdio")


def _check_reserved(name: str) -> None:
    if name in _RESERVED_NAMES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{name}' is reserved by a built-in MCP server",
        )


@router.post("/servers/user", status_code=status.HTTP_201_CREATED)
async def create_user_server(
    payload: UserServerCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _check_reserved(payload.name)
    _validate_payload(payload.transport, payload.url, payload.command)

    row = MCPServer(
        name=payload.name,
        transport=payload.transport,
        url=payload.url if payload.transport == "streamable_http" else None,
        command=payload.command if payload.transport == "stdio" else None,
        args_json=json.dumps(payload.args),
        headers_json=json.dumps(payload.headers),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An MCP server named '{payload.name}' already exists.",
        ) from None
    await session.refresh(row)

    manager = get_mcp_client_manager()
    apply_to_manager(row, manager)
    entry = manager.get(payload.name)
    assert entry is not None
    enabled = await _load_enabled(session)
    base = manager.status_dict(entry, enabled=enabled.get(payload.name, False))
    return _enrich_with_user_meta(base, row)


@router.patch("/servers/user/{server_id}")
async def update_user_server(
    server_id: int,
    payload: UserServerUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await session.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    old_name = row.name

    if payload.name is not None and payload.name != row.name:
        _check_reserved(payload.name)
        row.name = payload.name
    if payload.transport is not None:
        row.transport = payload.transport
    if payload.url is not None:
        row.url = payload.url
    if payload.command is not None:
        row.command = payload.command
    if payload.args is not None:
        row.args_json = json.dumps(payload.args)
    if payload.headers is not None:
        row.headers_json = json.dumps(payload.headers)

    # Normalise fields against the (possibly updated) transport.
    if row.transport == "streamable_http":
        row.command = None
        row.args_json = "[]"
    elif row.transport == "stdio":
        row.url = None
        row.headers_json = "{}"
    _validate_payload(row.transport, row.url, row.command)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An MCP server named '{row.name}' already exists.",
        ) from None
    await session.refresh(row)

    manager = get_mcp_client_manager()
    if old_name != row.name:
        manager.unregister_user_entry(old_name)
        # Also drop any enabled flag under the old name.
        enabled = await _load_enabled(session)
        if old_name in enabled:
            enabled[row.name] = enabled.pop(old_name)
            await _store_enabled(session, enabled)
    apply_to_manager(row, manager)

    entry = manager.get(row.name)
    assert entry is not None
    enabled = await _load_enabled(session)
    base = manager.status_dict(entry, enabled=enabled.get(row.name, False))
    return _enrich_with_user_meta(base, row)


@router.delete("/servers/user/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    name = row.name
    await session.delete(row)
    await session.commit()

    manager = get_mcp_client_manager()
    manager.unregister_user_entry(name)
    enabled = await _load_enabled(session)
    if name in enabled:
        del enabled[name]
        await _store_enabled(session, enabled)
