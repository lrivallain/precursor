"""Application-settings router — runtime-editable preferences stored in DB."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import AppSetting
from precursor.backend.schemas import SettingsPayload, SettingsRead
from precursor.backend.services.app_settings import (
    DEFAULT_GITHUB_REPO,
    DEFAULT_ISSUE_ASSOCIATIONS_ENABLED,
    DEFAULT_ISSUE_CONTEXT_TTL_MINUTES,
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_TOOL_ROUNDS,
    MAX_TOOL_ROUNDS_CEILING,
    azure_stt_ready,
    resolve_azure_speech_endpoint,
    resolve_azure_speech_language,
    resolve_mcp_expose,
    resolve_mcp_http_enabled,
    resolve_system_settings,
)
from precursor.backend.services.cmd_runner import docker_available
from precursor.backend.services.github_auth import github_token_source
from precursor.backend.services.mcp.precursor_server import (
    http_endpoint_url,
    is_loopback_host,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Keys that contain secrets — never echoed back, only their presence is reported.
_SECRET_KEYS = {"api_keys"}


def _clamp_max_tool_rounds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_MAX_TOOL_ROUNDS
    n = int(value)
    if n < 1:
        return 1
    if n > MAX_TOOL_ROUNDS_CEILING:
        return MAX_TOOL_ROUNDS_CEILING
    return n


async def _load_all(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(select(AppSetting))
    return {row.key: json.loads(row.value) for row in result.scalars().all()}


async def _upsert(session: AsyncSession, key: str, value: Any) -> None:
    existing = await session.get(AppSetting, key)
    encoded = json.dumps(value)
    if existing is None:
        session.add(AppSetting(key=key, value=encoded))
    else:
        existing.value = encoded


def _as_read(data: dict[str, Any], system: dict[str, Any], docker_ok: bool) -> SettingsRead:
    api_keys = data.get("api_keys") or {}
    return SettingsRead(
        theme=data.get("theme", "system"),
        llm_model=data.get("llm_model", DEFAULT_LLM_MODEL),
        github_repo=data.get("github_repo", DEFAULT_GITHUB_REPO),
        issue_context_ttl_minutes=data.get(
            "issue_context_ttl_minutes", DEFAULT_ISSUE_CONTEXT_TTL_MINUTES
        ),
        show_chat_stats=bool(data.get("show_chat_stats", True)),
        max_tool_rounds=_clamp_max_tool_rounds(
            data.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
        ),
        mcp_enabled=data.get("mcp_enabled", {}),
        mcp_servers=data.get("mcp_servers", {}),
        api_keys_present={k: bool(v) for k, v in api_keys.items()},
        github_token_source=github_token_source(get_settings()),
        issue_associations_enabled=bool(
            data.get("issue_associations_enabled", DEFAULT_ISSUE_ASSOCIATIONS_ENABLED)
        ),
        docker_available=docker_ok,
        **system,
    )


async def _mcp_http_block(session: AsyncSession) -> dict[str, Any]:
    cfg = get_settings()
    return {
        "mcp_http_enabled": await resolve_mcp_http_enabled(session),
        "mcp_http_url": http_endpoint_url(),
        "mcp_http_loopback_ok": is_loopback_host(cfg.host),
    }


async def _stt_block(session: AsyncSession) -> dict[str, Any]:
    return {
        "azure_speech_endpoint": await resolve_azure_speech_endpoint(session),
        "azure_speech_language": await resolve_azure_speech_language(session),
        "stt_azure_ready": await azure_stt_ready(session),
    }


@router.get("", response_model=SettingsRead)
async def read_settings(session: AsyncSession = Depends(get_session)) -> SettingsRead:
    data = await _load_all(session)
    system = await resolve_system_settings(session)
    system["mcp_expose"] = await resolve_mcp_expose(session)
    system.update(await _mcp_http_block(session))
    system.update(await _stt_block(session))
    return _as_read(data, system, docker_available()[0])


@router.put("", response_model=SettingsRead)
async def update_settings(
    payload: SettingsPayload,
    session: AsyncSession = Depends(get_session),
) -> SettingsRead:
    data = payload.model_dump(exclude_unset=True)

    # Merge api_keys instead of replacing — clients may PATCH a single key.
    if "api_keys" in data:
        current = await _load_all(session)
        merged = {**(current.get("api_keys") or {}), **data["api_keys"]}
        data["api_keys"] = {k: v for k, v in merged.items() if v}

    for key, value in data.items():
        await _upsert(session, key, value)
    await session.commit()
    refreshed = await _load_all(session)
    system = await resolve_system_settings(session)
    system["mcp_expose"] = await resolve_mcp_expose(session)
    system.update(await _mcp_http_block(session))
    system.update(await _stt_block(session))
    return _as_read(refreshed, system, docker_available()[0])
