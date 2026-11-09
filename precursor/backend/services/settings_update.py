"""Saving the settings panel: normalise a partial update, then apply it live.

``PUT /api/settings`` sends only the fields that changed. Before they are
stored, some need validating or merging with what is saved; after the commit,
some have to reach a running component (the agents runtime, a ticker, an MCP
server) so the change takes effect without a restart. Each rule is a small
function, listed in order in ``_NORMALISERS`` and ``_REACTIONS``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AppSetting
from precursor.backend.schemas.model_fallback import ModelCategories
from precursor.backend.services.agents.manager import get_agent_manager
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.github_auth import invalidate_gh_cli_token, resolve_github_token
from precursor.backend.services.llm.registry import PROVIDERS
from precursor.backend.services.model_catalog import invalidate_model_catalog
from precursor.backend.services.model_fallbacks import (
    resolve_model_category,
    resolve_model_fallbacks,
    scoped_categories,
    selected_category_presets,
)

logger = logging.getLogger(__name__)

Settings = dict[str, Any]


async def load_stored_settings(session: AsyncSession) -> Settings:
    """Every stored setting, decoded."""
    result = await session.execute(select(AppSetting))
    return {row.key: json.loads(row.value) for row in result.scalars().all()}


# -- Before saving -----------------------------------------------------------


def _clear_category_on_model_pick(data: Settings) -> None:
    # Picking a model by hand ends the category selection, unless both are sent.
    for model_key, category_key in (
        ("llm_model", "llm_model_category"),
        ("agents_default_model", "agents_model_category"),
    ):
        if model_key in data and category_key not in data:
            data[category_key] = None


async def _check_category(
    session: AsyncSession, data: Settings, category_key: str, scope: str
) -> None:
    if "model_fallbacks" in data:
        categories = scoped_categories(
            ModelCategories.model_validate(data["model_fallbacks"]), scope
        )
    else:
        categories = await resolve_model_fallbacks(session, scope)
    selected = data.get(category_key)
    if selected is not None and not selected_category_presets(categories, selected):
        raise HTTPException(
            status_code=422,
            detail=f"No presets are configured for the {selected!r} category.",
        )
    # Deleting or emptying a category must not leave a dangling selection.
    if selected is None and category_key not in data and "model_fallbacks" in data:
        saved_category = await resolve_model_category(session, agents=scope == "agents")
        if saved_category is not None and not selected_category_presets(categories, saved_category):
            data[category_key] = None


async def _check_categories(session: AsyncSession, data: Settings) -> None:
    _clear_category_on_model_pick(data)
    for category_key, scope in (("llm_model_category", "llm"), ("agents_model_category", "agents")):
        await _check_category(session, data, category_key, scope)


async def _check_iq_providers(session: AsyncSession, data: Settings) -> None:
    for provider_key in ("iq_embedding_provider", "iq_ask_provider"):
        if data.get(provider_key) and data[provider_key] not in PROVIDERS:
            raise HTTPException(status_code=422, detail=f"Unknown LLM provider for {provider_key}.")


async def _merge_api_keys(session: AsyncSession, data: Settings) -> None:
    # Merge instead of replacing: clients may PATCH a single key.
    if "api_keys" not in data:
        return
    current = await load_stored_settings(session)
    merged = {**(current.get("api_keys") or {}), **data["api_keys"]}
    data["api_keys"] = {k: v for k, v in merged.items() if v}
    # Clearing the saved token falls back to the CLI: re-read it rather than
    # serving whatever was cached before.
    invalidate_gh_cli_token()
    # A tokenless provider resolves to the mock, whose one-entry catalogue
    # must not outlive the credential that was just added or removed.
    invalidate_model_catalog()


def _merge_provider_fields(existing: dict[str, str], update: dict[str, str] | None) -> None:
    for key, value in (update or {}).items():
        if value == "":
            existing.pop(key, None)
        else:
            existing[key] = value


async def _merge_llm_providers(session: AsyncSession, data: Settings) -> None:
    # Deep-merge per provider so a partial update (e.g. one field) doesn't drop
    # the rest; an empty-string value clears a field.
    if "llm_providers" not in data:
        return
    current = await load_stored_settings(session)
    stored = current.get("llm_providers")
    merged: dict[str, dict[str, str]] = dict(stored) if isinstance(stored, dict) else {}
    for provider_id, cfg in (data["llm_providers"] or {}).items():
        existing = dict(merged.get(provider_id) or {})
        _merge_provider_fields(existing, cfg)
        merged[provider_id] = existing
    data["llm_providers"] = merged
    # Credentials moved: whatever catalogue that provider served is stale.
    invalidate_model_catalog()


async def _provider_switch(session: AsyncSession, data: Settings) -> None:
    # Switching provider changes which catalogue is authoritative.
    if "llm_provider" in data:
        invalidate_model_catalog()


_NORMALISERS: tuple[Callable[[AsyncSession, Settings], Awaitable[None]], ...] = (
    _check_categories,
    _check_iq_providers,
    _merge_api_keys,
    _merge_llm_providers,
    _provider_switch,
)


async def normalise_settings_update(session: AsyncSession, data: Settings) -> None:
    """Validate ``data`` and merge it with what is stored, in place.

    Raises a 422 for a value that can't be saved.
    """
    for normalise in _NORMALISERS:
        await normalise(session, data)


# -- After saving ------------------------------------------------------------


async def _reconcile_agents(session: AsyncSession) -> None:
    # Both start and stop are idempotent and a no-op when agents are unavailable.
    manager = get_agent_manager()
    if await resolve_agents_enabled(session):
        await manager.start()
    else:
        await manager.stop()


async def _apply_agent_overrides(session: AsyncSession) -> None:
    # Idle live agent sessions pick the change up on their next message.
    await get_agent_manager().apply_session_overrides()


async def _nudge_backup(session: AsyncSession) -> None:
    # The first backup runs now rather than up to a day later; the nudge only
    # runs one if it is actually due.
    from precursor.backend.services.backup_ticker import get_backup_ticker

    await get_backup_ticker().nudge()


async def _nudge_iq(session: AsyncSession) -> None:
    # Start vectorising now rather than on the next indexer tick.
    from precursor.backend.services.iq.ticker import get_iq_ticker

    await get_iq_ticker().nudge()


async def _reconfigure_agent365(session: AsyncSession) -> None:
    # A fresh tenant takes effect without a restart; clearing it disables the
    # servers cleanly.
    from precursor.backend.services.mcp.agent365 import configure_agent365_servers

    await configure_agent365_servers()


async def _refresh_precursor_tools(session: AsyncSession) -> None:
    # The precursor server advertises only the exposed sections, so its cached
    # tool list is stale: restart its session and re-list, so the count in
    # Settings (and the tools a chat offers the model) follow the toggles.
    from precursor.backend.routers.mcp import _load_enabled
    from precursor.backend.services.mcp.client import get_mcp_client_manager

    mcp_manager = get_mcp_client_manager()
    await mcp_manager.retire_worker("precursor")
    if (await _load_enabled(session)).get("precursor", False):
        await mcp_manager.probe("precursor", github_token=await resolve_github_token(session))


async def _reconfigure_playwright(session: AsyncSession) -> None:
    # Re-point the built-in server at the chosen browser channel (retiring the
    # warm worker, if any).
    from precursor.backend.services.mcp.client import configure_playwright_server

    await configure_playwright_server()


def _any_key(*keys: str) -> Callable[[Settings], bool]:
    return lambda data: any(k in data for k in keys)


def _iq_changed(data: Settings) -> bool:
    return bool(data.get("iq_embeddings_enabled")) or _any_key(
        "iq_embedding_model", "iq_embedding_provider"
    )(data)


@dataclass(frozen=True, slots=True)
class _Reaction:
    applies: Callable[[Settings], bool]
    run: Callable[[AsyncSession], Awaitable[None]]
    failure: str


# In order. The preference is already persisted when these run, so a runtime
# hiccup must not fail the save: each failure is logged and the next one runs.
_REACTIONS: tuple[_Reaction, ...] = (
    _Reaction(
        _any_key("agents_enabled"),
        _reconcile_agents,
        "Agents runtime reconcile failed after settings update",
    ),
    _Reaction(
        _any_key(
            "agents_default_model",
            "agents_reasoning_effort",
            "agents_context_tier",
            "agents_model_category",
            "model_fallbacks",
        ),
        _apply_agent_overrides,
        "Applying agent session overrides failed after settings update",
    ),
    _Reaction(
        _any_key("backup_enabled", "backup_dir"),
        _nudge_backup,
        "Backup nudge failed after settings update",
    ),
    _Reaction(_iq_changed, _nudge_iq, "Precursor IQ nudge failed after settings update"),
    _Reaction(
        _any_key("workiq_tenant_id"),
        _reconfigure_agent365,
        "Agent 365 reconfigure failed after settings update",
    ),
    _Reaction(
        _any_key("mcp_expose"),
        _refresh_precursor_tools,
        "Precursor MCP tool refresh failed after settings update",
    ),
    _Reaction(
        _any_key("playwright_browser"),
        _reconfigure_playwright,
        "Playwright reconfigure failed after settings update",
    ),
)


async def apply_saved_settings(session: AsyncSession, data: Settings) -> None:
    """Bring running components in line with the settings just saved."""
    for reaction in _REACTIONS:
        if not reaction.applies(data):
            continue
        try:
            await reaction.run(session)
        except Exception:
            logger.exception(reaction.failure)
