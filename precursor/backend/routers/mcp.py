"""HTTP-facing MCP endpoints.

Lets the frontend Settings panel list configured servers, toggle them on/off
(persisted in app settings under ``mcp_enabled``), probe a server to refresh
its connection state + tool catalog, and CRUD user-defined entries.
"""

from __future__ import annotations

import json
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
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    manager = get_mcp_client_manager()
    enabled = await _load_enabled(session)
    github_token = await resolve_github_token(session)
    # Lazily probe enabled servers with an empty tool catalogue (e.g. after a
    # process restart). The chat router itself opens fresh sessions, but the
    # UI relies on the cached tools list to render the catalogue.
    for entry in manager.list_entries():
        if enabled.get(entry.name, False) and not entry.tools:
            await manager.probe(entry.name, github_token=github_token)

    out: list[dict[str, Any]] = []
    for entry in manager.list_entries():
        base = manager.status_dict(entry, enabled=enabled.get(entry.name, False))
        row = None if entry.builtin else await get_row_by_name(session, entry.name)
        out.append(_enrich_with_user_meta(base, row))
    return out


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
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Restart the WorkIQ OAuth sign-in on an explicit user action.

    Background connects never pop a browser (they surface ``needs_auth``); this
    endpoint runs the interactive authorization-code grant, persists the fresh
    tokens, then rebuilds the background provider and re-probes so the next chat
    turn reuses the new session. Blocks until the browser flow completes.
    """
    from precursor.backend.services.mcp.workiq_preview import (
        WorkIQAuthInProgressError,
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

    try:
        await reauthenticate_workiq()
    except WorkIQAuthInProgressError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"WorkIQ sign-in failed: {exc}") from exc

    # Swap in a fresh background (non-interactive) provider so it reads the newly
    # persisted tokens, drop the stale warm worker, then refresh the catalog.
    manager.configure_workiq_preview(True, auth_provider=build_oauth_provider())
    await manager.retire_worker("workiq")

    enabled = await _load_enabled(session)
    is_enabled = enabled.get("workiq", False)
    if is_enabled:
        await manager.probe("workiq", github_token=await resolve_github_token(session))

    entry = manager.get("workiq")
    assert entry is not None
    # Wake any chat turn paused waiting for this sign-in so it resumes with the
    # freshly authenticated tools instead of timing out.
    manager.signal_auth_resolved()
    base = manager.status_dict(entry, enabled=is_enabled)
    return _enrich_with_user_meta(base, None)


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
