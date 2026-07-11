"""Application-settings router — runtime-editable preferences stored in DB."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import AppSetting
from precursor.backend.schemas import SettingsPayload, SettingsRead
from precursor.backend.services.agents.manager import get_agent_manager
from precursor.backend.services.agents.runtime import agents_available
from precursor.backend.services.app_settings import (
    DEFAULT_GITHUB_REPO,
    DEFAULT_ISSUE_ASSOCIATIONS_ENABLED,
    DEFAULT_ISSUE_CONTEXT_TTL_MINUTES,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_REASONING_EFFORT,
    DEFAULT_MAX_TOOL_ROUNDS,
    MAX_TOOL_ROUNDS_CEILING,
    azure_stt_ready,
    redact_llm_providers,
    resolve_agents_approval_policy,
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_enabled,
    resolve_agents_reasoning_effort,
    resolve_agents_system_prompt,
    resolve_agents_watchdog_timeout,
    resolve_azure_speech_endpoint,
    resolve_azure_speech_language,
    resolve_llm_provider,
    resolve_mcp_expose,
    resolve_mcp_http_enabled,
    resolve_system_settings,
)
from precursor.backend.services.backup import resolve_backup_status, run_backup
from precursor.backend.services.cmd_runner import docker_available
from precursor.backend.services.github_auth import github_token_source
from precursor.backend.services.mcp.precursor_server import (
    http_endpoint_url,
    is_loopback_host,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

logger = logging.getLogger(__name__)

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
        llm_reasoning_effort=data.get("llm_reasoning_effort", DEFAULT_LLM_REASONING_EFFORT),
        github_repo=data.get("github_repo", DEFAULT_GITHUB_REPO),
        issue_context_ttl_minutes=data.get(
            "issue_context_ttl_minutes", DEFAULT_ISSUE_CONTEXT_TTL_MINUTES
        ),
        show_chat_stats=bool(data.get("show_chat_stats", True)),
        notifications_enabled=bool(data.get("notifications_enabled", False)),
        max_tool_rounds=_clamp_max_tool_rounds(
            data.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
        ),
        mcp_enabled=data.get("mcp_enabled", {}),
        mcp_servers=data.get("mcp_servers", {}),
        api_keys_present={k: bool(v) for k, v in api_keys.items()},
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


async def _llm_block(session: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
    public, present = redact_llm_providers(data.get("llm_providers"))
    return {
        "github_token_source": await github_token_source(session),
        "llm_provider": await resolve_llm_provider(session),
        "llm_providers": public,
        "llm_providers_present": present,
    }


async def _agents_block(session: AsyncSession) -> dict[str, Any]:
    ok, detail = agents_available()
    return {
        "agents_enabled": await resolve_agents_enabled(session),
        "agents_available": ok,
        "agents_unavailable_reason": None if ok else detail,
        "agents_default_model": await resolve_agents_default_model(session),
        "agents_reasoning_effort": await resolve_agents_reasoning_effort(session),
        "agents_context_tier": await resolve_agents_context_tier(session),
        "agents_approval_policy": await resolve_agents_approval_policy(session),
        "agents_system_prompt": await resolve_agents_system_prompt(session),
        "agents_watchdog_timeout_seconds": await resolve_agents_watchdog_timeout(session),
    }


@router.get("", response_model=SettingsRead)
async def read_settings(session: AsyncSession = Depends(get_session)) -> SettingsRead:
    data = await _load_all(session)
    system = await resolve_system_settings(session)
    system["mcp_expose"] = await resolve_mcp_expose(session)
    system.update(await _mcp_http_block(session))
    system.update(await _stt_block(session))
    system.update(await _llm_block(session, data))
    system.update(await _agents_block(session))
    system.update(await resolve_backup_status(session))
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

    # Deep-merge llm_providers per provider so a partial update (e.g. one field)
    # doesn't drop the rest; an empty-string value clears a field.
    if "llm_providers" in data:
        current = await _load_all(session)
        stored = current.get("llm_providers")
        merged_providers: dict[str, dict[str, str]] = (
            dict(stored) if isinstance(stored, dict) else {}
        )
        for provider_id, cfg in (data["llm_providers"] or {}).items():
            existing = dict(merged_providers.get(provider_id) or {})
            for key, value in (cfg or {}).items():
                if value == "":
                    existing.pop(key, None)
                else:
                    existing[key] = value
            merged_providers[provider_id] = existing
        data["llm_providers"] = merged_providers

    for key, value in data.items():
        await _upsert(session, key, value)
    await session.commit()

    # Reconcile the agents runtime live so toggling the setting doesn't require a
    # restart (both start/stop are idempotent and a no-op when unavailable). The
    # preference is already persisted, so a runtime hiccup here must not fail the
    # save — log and carry on.
    if "agents_enabled" in data:
        manager = get_agent_manager()
        try:
            if await resolve_agents_enabled(session):
                await manager.start()
            else:
                await manager.stop()
        except Exception:
            logger.exception("Agents runtime reconcile failed after settings update")

    # Push model / reasoning-effort / context-tier changes onto idle live agent
    # sessions so they apply on the next message instead of only new sessions.
    if any(
        k in data
        for k in ("agents_default_model", "agents_reasoning_effort", "agents_context_tier")
    ):
        try:
            await get_agent_manager().apply_session_overrides()
        except Exception:
            logger.exception("Applying agent session overrides failed after settings update")

    # Kick off a backup right away when the user just enabled it (or changed the
    # target folder while enabled), so they don't wait up to a day for the first
    # one. The ticker's nudge only runs if a backup is actually due.
    if any(k in data for k in ("backup_enabled", "backup_dir")):
        from precursor.backend.services.backup_ticker import get_backup_ticker

        try:
            await get_backup_ticker().nudge()
        except Exception:
            logger.exception("Backup nudge failed after settings update")

    refreshed = await _load_all(session)
    system = await resolve_system_settings(session)
    system["mcp_expose"] = await resolve_mcp_expose(session)
    system.update(await _mcp_http_block(session))
    system.update(await _stt_block(session))
    system.update(await _llm_block(session, refreshed))
    system.update(await _agents_block(session))
    system.update(await resolve_backup_status(session))
    return _as_read(refreshed, system, docker_available()[0])


@router.post("/backup/run")
async def run_backup_now() -> dict[str, Any]:
    """Run a folder backup immediately, ignoring the daily cadence.

    Returns the outcome so the Settings UI can surface success/failure inline.
    A ``skipped`` status means backups are disabled or misconfigured.
    """
    result = await run_backup()
    return {
        "ok": result.ok,
        "status": result.status,
        "detail": result.detail,
        "db_snapshot": result.db_snapshot,
        "blobs_copied": result.blobs_copied,
        "blobs_total": result.blobs_total,
    }
