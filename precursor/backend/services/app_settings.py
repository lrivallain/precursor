"""Resolve runtime app-settings values stored in the `AppSetting` DB table.

These values are written by the UI Settings panel. The constants below act as
factory defaults used until the user picks something in the UI.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.models import AppSetting
from precursor.backend.services.cmd_runner import CmdRunnerConfig

# Factory defaults — surface in the UI before the user has saved a preference.
DEFAULT_LLM_MODEL = "claude-sonnet-4.5"
DEFAULT_GITHUB_REPO = ""
# How long a cached issue context (summary + state + labels) stays fresh
# before being transparently refreshed on the next read. The user can always
# force a refresh from the Context tab.
DEFAULT_ISSUE_CONTEXT_TTL_MINUTES = 60
# Hard ceiling on tool-call iterations per user turn — prevents runaway loops
# while still leaving room for multi-step agents (browser automation,
# multi-search workflows, etc.).
DEFAULT_MAX_TOOL_ROUNDS = 15
MAX_TOOL_ROUNDS_CEILING = 50
# When false, the GitHub-issue association feature is hidden across the UI
# and all related API endpoints reject requests. Existing topic links and
# cached contexts are preserved so the user can re-enable later.
DEFAULT_ISSUE_ASSOCIATIONS_ENABLED = True

# Sections of Precursor's *own* capabilities that the built-in "precursor" MCP
# server can expose to callers (the in-app agent and external MCP hosts). Each
# is opt-in (default False) — serving conversation history / write actions
# outbound is a deliberate disclosure, so nothing is exposed until the user
# turns it on in Settings.
MCP_EXPOSE_SECTIONS: tuple[str, ...] = (
    "topics",
    "messages",
    "search",
    "skills",
    "memory",
    "post_message",
    "schedules",
)
DEFAULT_MCP_EXPOSE: dict[str, bool] = {s: False for s in MCP_EXPOSE_SECTIONS}

# Whether the built-in "precursor" MCP server is also served over HTTP
# (streamable-http at /mcp), in addition to stdio. Default off; the endpoint is
# unauthenticated and only answers on the app's loopback bind.
DEFAULT_MCP_HTTP_ENABLED = False


async def _get_db_value(session: AsyncSession, key: str) -> Any | None:
    row = await session.get(AppSetting, key)
    if row is None:
        return None
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return None


async def resolve_global_github_repo(session: AsyncSession) -> str:
    """Return the effective global `owner/name` repo, or `""` if unset."""
    db_value = await _get_db_value(session, "github_repo")
    if isinstance(db_value, str) and db_value.strip():
        return db_value.strip()
    return DEFAULT_GITHUB_REPO


async def resolve_llm_model(session: AsyncSession) -> str:
    """Return the user-selected LLM model id, or the factory default."""
    db_value = await _get_db_value(session, "llm_model")
    if isinstance(db_value, str) and db_value.strip():
        return db_value.strip()
    return DEFAULT_LLM_MODEL


async def resolve_llm_provider(session: AsyncSession) -> str:
    """Return the active LLM provider id, or the factory default."""
    from precursor.backend.services.llm.registry import DEFAULT_PROVIDER, PROVIDERS

    db_value = await _get_db_value(session, "llm_provider")
    if isinstance(db_value, str) and db_value in PROVIDERS:
        return db_value
    return DEFAULT_PROVIDER


async def resolve_llm_provider_config(session: AsyncSession, provider_id: str) -> dict[str, str]:
    """Return the stored config dict for ``provider_id`` (incl. secrets)."""
    db_value = await _get_db_value(session, "llm_providers")
    if isinstance(db_value, dict):
        cfg = db_value.get(provider_id)
        if isinstance(cfg, dict):
            return {k: v for k, v in cfg.items() if isinstance(v, str)}
    return {}


def redact_llm_providers(
    stored: object,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, bool]]]:
    """Split stored provider configs into (public fields, secret-presence map).

    Secret field values are never returned to the client — only a boolean per
    secret field indicating whether it's set.
    """
    from precursor.backend.services.llm.registry import provider_secret_fields

    public: dict[str, dict[str, str]] = {}
    present: dict[str, dict[str, bool]] = {}
    if not isinstance(stored, dict):
        return public, present
    for provider_id, cfg in stored.items():
        if not isinstance(cfg, dict):
            continue
        secrets = provider_secret_fields(provider_id)
        pub: dict[str, str] = {}
        pres: dict[str, bool] = {}
        for key, value in cfg.items():
            if not isinstance(value, str):
                continue
            if key in secrets:
                pres[key] = bool(value)
            else:
                pub[key] = value
        public[provider_id] = pub
        if pres:
            present[provider_id] = pres
    return public, present


async def resolve_issue_context_ttl_minutes(session: AsyncSession) -> int:
    """Return the configured issue-context TTL, clamped to a sane range."""
    db_value = await _get_db_value(session, "issue_context_ttl_minutes")
    if isinstance(db_value, (int, float)) and db_value > 0:
        return max(1, min(int(db_value), 60 * 24 * 7))
    return DEFAULT_ISSUE_CONTEXT_TTL_MINUTES


async def resolve_max_tool_rounds(session: AsyncSession) -> int:
    """Return the configured tool-call round cap, clamped to a sane range."""
    db_value = await _get_db_value(session, "max_tool_rounds")
    if isinstance(db_value, (int, float)) and db_value >= 1:
        return max(1, min(int(db_value), MAX_TOOL_ROUNDS_CEILING))
    return DEFAULT_MAX_TOOL_ROUNDS


async def resolve_issue_associations_enabled(session: AsyncSession) -> bool:
    """Return whether the GitHub-issue association feature is enabled."""
    db_value = await _get_db_value(session, "issue_associations_enabled")
    if isinstance(db_value, bool):
        return db_value
    return DEFAULT_ISSUE_ASSOCIATIONS_ENABLED


async def resolve_azure_speech_key(session: AsyncSession) -> str:
    """Effective Azure Speech key from the DB ``api_keys`` (Settings panel)."""
    api_keys = await _get_db_value(session, "api_keys")
    if isinstance(api_keys, dict):
        key = api_keys.get("azure_speech_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    return ""


async def resolve_azure_speech_endpoint(session: AsyncSession) -> str:
    """Effective Azure Speech endpoint URL from the DB (Settings panel)."""
    db_value = await _get_db_value(session, "azure_speech_endpoint")
    if isinstance(db_value, str) and db_value.strip():
        return db_value.strip()
    return ""


async def resolve_azure_speech_language(session: AsyncSession) -> str:
    """Effective dictation language (BCP-47, e.g. ``en-US``); ``""`` => auto."""
    db_value = await _get_db_value(session, "azure_speech_language")
    if isinstance(db_value, str) and db_value.strip():
        return db_value.strip()
    return ""


async def azure_stt_ready(session: AsyncSession) -> bool:
    """True when both an Azure Speech key and endpoint are configured."""
    return bool(
        await resolve_azure_speech_key(session) and await resolve_azure_speech_endpoint(session)
    )


# -- System settings (env-default + DB override) ---------------------------
#
# These mirror fields on ``config.Settings`` (env / .env). The env value is the
# factory default; a DB row written from the Settings → System panel overrides
# it at runtime. Helpers below clamp to sane ranges so a bad value can't wedge
# the app.

# Clamp bounds.
_MIN_INPUT_TOKENS, _MAX_INPUT_TOKENS = 1_000, 5_000_000
_MIN_TOOL_RESULT_TOKENS, _MAX_TOOL_RESULT_TOKENS = 100, 2_000_000
_MIN_RUN_TIMEOUT, _MAX_RUN_TIMEOUT = 10, 24 * 3600
_MIN_CMD_TIMEOUT, _MAX_CMD_TIMEOUT = 1, 3600
_MIN_CMD_OUTPUT, _MAX_CMD_OUTPUT = 1_000, 50_000_000
_MIN_PIDS, _MAX_PIDS = 1, 100_000


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(lo, min(int(value), hi))


async def resolve_llm_max_input_tokens(session: AsyncSession) -> int:
    default = get_settings().llm_max_input_tokens
    db_value = await _get_db_value(session, "llm_max_input_tokens")
    return _clamp_int(db_value, default, _MIN_INPUT_TOKENS, _MAX_INPUT_TOKENS)


async def resolve_llm_max_tool_result_tokens(session: AsyncSession) -> int:
    default = get_settings().llm_max_tool_result_tokens
    db_value = await _get_db_value(session, "llm_max_tool_result_tokens")
    return _clamp_int(db_value, default, _MIN_TOOL_RESULT_TOKENS, _MAX_TOOL_RESULT_TOKENS)


async def resolve_scheduled_run_timeout_seconds(session: AsyncSession) -> int:
    default = get_settings().scheduled_run_timeout_seconds
    db_value = await _get_db_value(session, "scheduled_run_timeout_seconds")
    return _clamp_int(db_value, default, _MIN_RUN_TIMEOUT, _MAX_RUN_TIMEOUT)


async def resolve_cmd_runner_config(session: AsyncSession) -> CmdRunnerConfig:
    """Effective cmd-runner config: env defaults with DB overrides applied."""
    settings = get_settings()
    jail = await _get_db_value(session, "cmd_runner_jail")
    image = await _get_db_value(session, "cmd_runner_image")
    network = await _get_db_value(session, "cmd_runner_network")
    memory = await _get_db_value(session, "cmd_runner_memory")
    cpus = await _get_db_value(session, "cmd_runner_cpus")
    return CmdRunnerConfig(
        jail=jail if isinstance(jail, bool) else settings.cmd_runner_jail,
        image=(
            image.strip() if isinstance(image, str) and image.strip() else settings.cmd_runner_image
        ),
        network=network if isinstance(network, bool) else settings.cmd_runner_network,
        timeout_seconds=_clamp_int(
            await _get_db_value(session, "cmd_runner_timeout_seconds"),
            settings.cmd_runner_timeout_seconds,
            _MIN_CMD_TIMEOUT,
            _MAX_CMD_TIMEOUT,
        ),
        max_output_bytes=_clamp_int(
            await _get_db_value(session, "cmd_runner_max_output_bytes"),
            settings.cmd_runner_max_output_bytes,
            _MIN_CMD_OUTPUT,
            _MAX_CMD_OUTPUT,
        ),
        memory=(
            memory.strip()
            if isinstance(memory, str) and memory.strip()
            else settings.cmd_runner_memory
        ),
        pids_limit=_clamp_int(
            await _get_db_value(session, "cmd_runner_pids_limit"),
            settings.cmd_runner_pids_limit,
            _MIN_PIDS,
            _MAX_PIDS,
        ),
        cpus=(cpus.strip() if isinstance(cpus, str) and cpus.strip() else settings.cmd_runner_cpus),
    )


async def resolve_system_settings(session: AsyncSession) -> dict[str, Any]:
    """All effective "System" settings (env defaults + DB overrides) for the UI."""
    cfg = await resolve_cmd_runner_config(session)
    return {
        "llm_max_input_tokens": await resolve_llm_max_input_tokens(session),
        "llm_max_tool_result_tokens": await resolve_llm_max_tool_result_tokens(session),
        "scheduled_run_timeout_seconds": await resolve_scheduled_run_timeout_seconds(session),
        "cmd_runner_jail": cfg.jail,
        "cmd_runner_image": cfg.image,
        "cmd_runner_network": cfg.network,
        "cmd_runner_timeout_seconds": cfg.timeout_seconds,
        "cmd_runner_max_output_bytes": cfg.max_output_bytes,
        "cmd_runner_memory": cfg.memory,
        "cmd_runner_pids_limit": cfg.pids_limit,
        "cmd_runner_cpus": cfg.cpus,
    }


async def resolve_mcp_expose(session: AsyncSession) -> dict[str, bool]:
    """Which Precursor capability sections the built-in MCP server may serve.

    Returns every known section with an explicit boolean (default False),
    overlaying any DB-stored values. Unknown keys in the DB are ignored.
    """
    out = dict(DEFAULT_MCP_EXPOSE)
    db_value = await _get_db_value(session, "mcp_expose")
    if isinstance(db_value, dict):
        for key in out:
            if isinstance(db_value.get(key), bool):
                out[key] = db_value[key]
    return out


async def resolve_mcp_http_enabled(session: AsyncSession) -> bool:
    """Whether the built-in 'precursor' MCP server is served over HTTP too."""
    db_value = await _get_db_value(session, "mcp_http_enabled")
    if isinstance(db_value, bool):
        return db_value
    return DEFAULT_MCP_HTTP_ENABLED


async def resolve_agents_enabled(session: AsyncSession) -> bool:
    """Whether Agents mode (Copilot SDK) is enabled.

    DB override on top of the ``agents_enabled`` env default. Note this is the
    *preference*; whether the runtime is actually usable is a separate capability
    probe (``services.agents.runtime.agents_available``).
    """
    db_value = await _get_db_value(session, "agents_enabled")
    if isinstance(db_value, bool):
        return db_value
    return get_settings().agents_enabled


async def resolve_agents_default_model(session: AsyncSession) -> str:
    """Default model for new agent sessions (DB override on env default)."""
    db_value = await _get_db_value(session, "agents_default_model")
    if isinstance(db_value, str) and db_value.strip():
        return db_value
    return get_settings().agents_default_model


AGENTS_APPROVAL_POLICIES = ("manual", "balanced", "autonomous")


async def resolve_agents_approval_policy(session: AsyncSession) -> str:
    """Default approval policy gating agent actions (DB override on env default).

    See ``Settings.agents_approval_policy``: ``manual`` asks for everything,
    ``balanced`` auto-approves read-only actions, ``autonomous`` approves all.
    """
    db_value = await _get_db_value(session, "agents_approval_policy")
    if isinstance(db_value, str) and db_value in AGENTS_APPROVAL_POLICIES:
        return db_value
    default = get_settings().agents_approval_policy
    return default if default in AGENTS_APPROVAL_POLICIES else "balanced"


async def resolve_agents_system_prompt(session: AsyncSession) -> str:
    """Extra system-message preamble appended to every agent session.

    DB override on top of the ``agents_system_prompt`` env default. The SDK base
    prompt isn't ours to set, so this is appended (alongside any topic binding).
    """
    db_value = await _get_db_value(session, "agents_system_prompt")
    if isinstance(db_value, str):
        return db_value
    return get_settings().agents_system_prompt
