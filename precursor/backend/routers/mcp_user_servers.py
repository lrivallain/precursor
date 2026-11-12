"""Create, edit and delete user-defined MCP servers, and detect their sign-in."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.lookups import get_or_404
from precursor.backend.models import MCPServer
from precursor.backend.routers.mcp import _load_enabled, _store_enabled
from precursor.backend.schemas.mcp import (
    MCPAuthDetection,
    MCPAuthDetectRequest,
    UserServerCreate,
    UserServerUpdate,
)
from precursor.backend.services.azure_cli import check_tenant
from precursor.backend.services.mcp.client import BUILTIN_CATALOG, get_mcp_client_manager
from precursor.backend.services.mcp.entra_auth import detect_server_auth
from precursor.backend.services.mcp.user_servers import apply_to_manager, enrich_with_user_meta

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# Derived rather than listed: a hand-maintained copy drifts from the catalogue
# the moment a built-in is added (it had already lost ``drawio``), and the drift
# is silent — a user server shadows the built-in name instead of being rejected.
_RESERVED_NAMES = frozenset(spec.name for spec in BUILTIN_CATALOG)


def _validate_row(row: MCPServer) -> None:
    if row.transport == "streamable_http" and not row.url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url is required for streamable_http")
    if row.transport == "stdio" and not row.command:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "command is required for stdio")
    if row.auth == "azure_cli" and not row.auth_resource:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "resource is required for Entra ID (Azure CLI) sign-in",
        )
    try:
        row.auth_tenant = check_tenant(row.auth_tenant or "") or None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


def _normalize_row(row: MCPServer) -> None:
    """Clear the fields the (possibly updated) transport and auth mode don't use."""
    if row.transport == "streamable_http":
        row.command = None
        row.args_json = "[]"
    elif row.transport == "stdio":
        row.url = None
        row.headers_json = "{}"
        row.auth = "none"
    if row.auth != "azure_cli":
        row.auth_tenant = None
        row.auth_resource = None
    elif row.auth_resource:
        row.auth_resource = row.auth_resource.strip()


def _check_reserved(name: str) -> None:
    if name in _RESERVED_NAMES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{name}' is reserved by a built-in MCP server",
        )


async def _commit_unique(session: AsyncSession, row: MCPServer) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An MCP server named '{row.name}' already exists.",
        ) from None
    await session.refresh(row)


async def _status_of(session: AsyncSession, row: MCPServer) -> dict[str, Any]:
    manager = get_mcp_client_manager()
    apply_to_manager(row, manager)
    entry = manager.get(row.name)
    assert entry is not None
    enabled = await _load_enabled(session)
    base = manager.status_dict(entry, enabled=enabled.get(row.name, False))
    return enrich_with_user_meta(base, row)


@router.post("/servers/detect-auth", response_model=MCPAuthDetection)
async def detect_auth(payload: MCPAuthDetectRequest) -> MCPAuthDetection:
    """Probe an MCP URL for the sign-in it expects (Entra tenant and resource)."""
    return await detect_server_auth(payload.url)


@router.post("/servers/user", status_code=status.HTTP_201_CREATED)
async def create_user_server(
    payload: UserServerCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _check_reserved(payload.name)
    row = MCPServer(
        name=payload.name,
        transport=payload.transport,
        url=payload.url,
        command=payload.command,
        args_json=json.dumps(payload.args),
        headers_json=json.dumps(payload.headers),
        auth=payload.auth,
        auth_tenant=payload.auth_tenant,
        auth_resource=payload.auth_resource,
    )
    _normalize_row(row)
    _validate_row(row)
    session.add(row)
    await _commit_unique(session, row)
    return await _status_of(session, row)


_PLAIN_FIELDS = ("transport", "url", "command", "auth", "auth_tenant", "auth_resource")


@router.patch("/servers/user/{server_id}")
async def update_user_server(
    server_id: int,
    payload: UserServerUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await get_or_404(session, MCPServer, server_id, "MCP server not found")
    old_name = row.name

    if payload.name is not None and payload.name != row.name:
        _check_reserved(payload.name)
        row.name = payload.name
    for field in _PLAIN_FIELDS:
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    if payload.args is not None:
        row.args_json = json.dumps(payload.args)
    if payload.headers is not None:
        row.headers_json = json.dumps(payload.headers)
    _normalize_row(row)
    _validate_row(row)
    await _commit_unique(session, row)

    if old_name != row.name:
        get_mcp_client_manager().unregister_user_entry(old_name)
        # Carry the enabled flag over to the new name.
        enabled = await _load_enabled(session)
        if old_name in enabled:
            enabled[row.name] = enabled.pop(old_name)
            await _store_enabled(session, enabled)
    return await _status_of(session, row)


@router.delete("/servers/user/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_server(
    server_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await get_or_404(session, MCPServer, server_id, "MCP server not found")
    name = row.name
    await session.delete(row)
    await session.commit()

    get_mcp_client_manager().unregister_user_entry(name)
    enabled = await _load_enabled(session)
    if name in enabled:
        del enabled[name]
        await _store_enabled(session, enabled)
