"""Application-settings router — runtime-editable preferences stored in DB."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.db import get_session
from precursor.backend.models import AppSetting
from precursor.backend.schemas import SettingsPayload, SettingsRead
from precursor.backend.schemas.model_fallback import normalize_stored_categories
from precursor.backend.services.agents.manager import get_agent_manager
from precursor.backend.services.agents.runtime import agents_available
from precursor.backend.services.app_settings import (
    DEFAULT_CHAT_AUTONAME_ENABLED,
    DEFAULT_GITHUB_REPO,
    DEFAULT_ISSUE_ASSOCIATIONS_ENABLED,
    DEFAULT_ISSUE_CONTEXT_TTL_MINUTES,
    DEFAULT_LLM_REASONING_EFFORT,
    DEFAULT_MAX_TOOL_ROUNDS,
    MAX_TOOL_ROUNDS_CEILING,
    redact_llm_providers,
    resolve_agents_approval_policy,
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_enabled,
    resolve_agents_file_change_tracking,
    resolve_agents_reasoning_effort,
    resolve_agents_system_prompt,
    resolve_agents_watchdog_timeout,
    resolve_chat_approval_policy,
    resolve_iq_ask_model,
    resolve_iq_ask_provider,
    resolve_iq_embedding_model,
    resolve_iq_embedding_provider,
    resolve_iq_embeddings_enabled,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_mcp_expose,
    resolve_mcp_http_enabled,
    resolve_openai_proxy_enabled,
    resolve_openai_proxy_key,
    resolve_plugin_install_enabled,
    resolve_scheduled_approval_timeout,
    resolve_system_settings,
    resolve_topic_approval_policy,
    resolve_workflows_default_capabilities,
    resolve_workflows_default_step_timeout,
    resolve_workiq_tenant_id,
)
from precursor.backend.services.backup import resolve_backup_status, run_backup
from precursor.backend.services.cmd_runner import docker_available
from precursor.backend.services.github_auth import github_token_source
from precursor.backend.services.llm.registry import with_implied_values
from precursor.backend.services.mcp.precursor_server import (
    http_endpoint_url,
    is_loopback_host,
)
from precursor.backend.services.model_fallbacks import (
    preview_llm_category_preset,
    resolve_model_category,
)
from precursor.backend.services.openai_proxy import (
    ensure_proxy_key,
    proxy_availability,
    proxy_base_url,
    rotate_proxy_key,
)
from precursor.backend.services.settings_update import (
    apply_saved_settings,
    normalise_settings_update,
)
from precursor.backend.services.settings_update import load_stored_settings as _load_all
from precursor.backend.services.speech_settings import (
    resolve_azure_speech_language,
    resolve_speech_config,
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


async def _upsert(session: AsyncSession, key: str, value: Any) -> None:
    existing = await session.get(AppSetting, key)
    encoded = json.dumps(value)
    if existing is None:
        session.add(AppSetting(key=key, value=encoded))
    else:
        existing.value = encoded


def _as_read(
    data: dict[str, Any], system: dict[str, Any], docker_ok: bool, llm_model: str
) -> SettingsRead:
    api_keys = data.get("api_keys") or {}
    return SettingsRead(
        theme=data.get("theme", "system"),
        font_family=data.get("font_family", "system"),
        # Resolved, not raw: nothing is stored on a fresh install and a stored
        # id may have been retired, so this is what a turn would actually use.
        llm_model=llm_model,
        model_fallbacks=normalize_stored_categories(data.get("model_fallbacks")),
        llm_reasoning_effort=data.get("llm_reasoning_effort", DEFAULT_LLM_REASONING_EFFORT),
        github_repo=data.get("github_repo", DEFAULT_GITHUB_REPO),
        issue_context_ttl_minutes=data.get(
            "issue_context_ttl_minutes", DEFAULT_ISSUE_CONTEXT_TTL_MINUTES
        ),
        show_chat_stats=bool(data.get("show_chat_stats", True)),
        show_quick_actions=bool(data.get("show_quick_actions", True)),
        notifications_enabled=bool(data.get("notifications_enabled", False)),
        completion_sound_enabled=bool(data.get("completion_sound_enabled", False)),
        max_tool_rounds=_clamp_max_tool_rounds(
            data.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
        ),
        mcp_enabled=data.get("mcp_enabled", {}),
        mcp_servers=data.get("mcp_servers", {}),
        mcp_defer_tools=bool(data.get("mcp_defer_tools", True)),
        mcp_always_load={str(k): bool(v) for k, v in (data.get("mcp_always_load") or {}).items()},
        api_keys_present={k: bool(v) for k, v in api_keys.items()},
        issue_associations_enabled=bool(
            data.get("issue_associations_enabled", DEFAULT_ISSUE_ASSOCIATIONS_ENABLED)
        ),
        live_fast_model=str(data.get("live_fast_model", "") or ""),
        live_reasoning_effort=str(data.get("live_reasoning_effort", "") or ""),
        live_enabled=bool(data.get("live_enabled", True)),
        chat_autoname_enabled=bool(
            data.get("chat_autoname_enabled", DEFAULT_CHAT_AUTONAME_ENABLED)
        ),
        mock_provider_enabled=bool(data.get("mock_provider_enabled", True)),
        chat_autoname_model=str(data.get("chat_autoname_model", "") or ""),
        docker_available=docker_ok,
        **system,
    )


async def _mcp_http_block(session: AsyncSession) -> dict[str, Any]:
    from precursor.backend.services.mcp.agent365 import discover_tenant_id

    cfg = get_settings()
    # Surface the *effective* tenant so the UI can show what the Agent 365
    # servers actually use — including one auto-discovered from a signed-in
    # token, which the user never typed anywhere.
    configured = (await resolve_workiq_tenant_id(session)).strip()
    tenant = configured or await discover_tenant_id()
    return {
        "mcp_http_enabled": await resolve_mcp_http_enabled(session),
        "plugin_install_enabled": await resolve_plugin_install_enabled(session),
        "mcp_http_url": http_endpoint_url(),
        "mcp_http_loopback_ok": is_loopback_host(cfg.host),
        "workiq_tenant_id": tenant,
        "workiq_tenant_discovered": bool(tenant) and not configured,
    }


async def _openai_proxy_block(session: AsyncSession) -> dict[str, Any]:
    availability = await proxy_availability(session)
    return {
        "openai_proxy_enabled": await resolve_openai_proxy_enabled(session),
        "openai_proxy_url": proxy_base_url(),
        "openai_proxy_key": await resolve_openai_proxy_key(session),
        "openai_proxy_available": availability.available,
        "openai_proxy_unavailable_reason": availability.reason,
    }


async def _iq_block(session: AsyncSession) -> dict[str, Any]:
    return {
        "iq_embeddings_enabled": await resolve_iq_embeddings_enabled(session),
        "iq_embedding_provider": await resolve_iq_embedding_provider(session),
        "iq_embedding_model": await resolve_iq_embedding_model(session),
        "iq_ask_provider": await resolve_iq_ask_provider(session),
        "iq_ask_model": await resolve_iq_ask_model(session),
    }


async def _stt_block(session: AsyncSession) -> dict[str, Any]:
    speech = await resolve_speech_config(session)
    return {
        "azure_speech_endpoint": speech.endpoint,
        "azure_speech_language": await resolve_azure_speech_language(session),
        "azure_speech_auth": speech.auth,
        "azure_speech_tenant": speech.tenant,
        "azure_speech_resource_id": speech.resource_id,
        "stt_azure_ready": speech.ready,
    }


async def _llm_block(session: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
    public, present = redact_llm_providers(data.get("llm_providers"))
    public = with_implied_values(data.get("llm_providers"), public)
    return {
        "github_token_source": await github_token_source(session),
        "llm_provider": await resolve_llm_provider(session),
        "llm_model_category": await resolve_model_category(session),
        "llm_category_preset": await preview_llm_category_preset(session),
        "llm_providers": public,
        "llm_providers_present": present,
    }


async def _agents_block(session: AsyncSession) -> dict[str, Any]:
    ok, detail = agents_available()
    # ``agents_available`` is a stateless capability probe (SDK package + CLI
    # binary present). ``agents_runtime_started`` reflects whether the manager
    # actually brought its SDK client + watchdog up in *this* process — it can be
    # False even when available is True (e.g. the client failed to start on a
    # reload), which is exactly when agents silently stop being driven.
    return {
        "agents_enabled": await resolve_agents_enabled(session),
        "agents_available": ok,
        "agents_runtime_started": get_agent_manager().ready,
        "agents_unavailable_reason": None if ok else detail,
        "agents_default_model": await resolve_agents_default_model(session),
        "agents_model_category": await resolve_model_category(session, agents=True),
        "agents_reasoning_effort": await resolve_agents_reasoning_effort(session),
        "agents_context_tier": await resolve_agents_context_tier(session),
        "agents_file_change_tracking": await resolve_agents_file_change_tracking(session),
        "agents_approval_policy": await resolve_agents_approval_policy(session),
        "topic_approval_policy": await resolve_topic_approval_policy(session),
        "chat_approval_policy": await resolve_chat_approval_policy(session),
        "scheduled_approval_timeout_minutes": await resolve_scheduled_approval_timeout(session),
        "agents_system_prompt": await resolve_agents_system_prompt(session),
        "agents_watchdog_timeout_seconds": await resolve_agents_watchdog_timeout(session),
        **{
            f"workflows_default_{k}": v
            for k, v in (await resolve_workflows_default_capabilities(session)).items()
        },
        "workflows_default_step_timeout_seconds": await resolve_workflows_default_step_timeout(
            session
        ),
    }


async def _settings_response(session: AsyncSession) -> SettingsRead:
    data = await _load_all(session)
    system = await resolve_system_settings(session)
    system["mcp_expose"] = await resolve_mcp_expose(session)
    system.update(await _mcp_http_block(session))
    system.update(await _openai_proxy_block(session))
    system.update(await _stt_block(session))
    system.update(await _iq_block(session))
    system.update(await _llm_block(session, data))
    system.update(await _agents_block(session))
    system.update(await resolve_backup_status(session))
    return _as_read(data, system, docker_available()[0], await resolve_llm_model(session))


@router.get("", response_model=SettingsRead)
async def read_settings(session: AsyncSession = Depends(get_session)) -> SettingsRead:
    return await _settings_response(session)


@router.put("", response_model=SettingsRead)
async def update_settings(
    payload: SettingsPayload,
    session: AsyncSession = Depends(get_session),
) -> SettingsRead:
    data = payload.model_dump(exclude_unset=True)
    await normalise_settings_update(session, data)
    for key, value in data.items():
        await _upsert(session, key, value)
    # First switch-on mints the key, so the endpoint is usable straight away.
    if data.get("openai_proxy_enabled"):
        await ensure_proxy_key(session)
    await session.commit()
    await apply_saved_settings(session, data)
    return await _settings_response(session)


@router.post("/openai-proxy/key")
async def regenerate_openai_proxy_key(
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Replace the OpenAI-compatible endpoint's API key.

    Clients holding the previous key are refused from the next request on.
    """
    key = await rotate_proxy_key(session)
    await session.commit()
    return {"key": key}


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
        "definitions_archive": result.definitions_archive,
    }
