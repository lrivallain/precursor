"""Application-settings schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Theme = Literal["light", "dark", "system"]
GitHubTokenSource = Literal["env", "gh-cli", "settings", "none"]


class SettingsPayload(BaseModel):
    """Partial update — every field is optional."""

    theme: Theme | None = None
    llm_model: str | None = None
    # Reasoning effort hint for reasoning-capable models: "" (auto/off — the
    # param is omitted), "low", "medium", or "high".
    llm_reasoning_effort: str | None = None
    # Active LLM provider id (see services/llm/registry.py).
    llm_provider: str | None = None
    # Per-provider config maps, e.g. {"azure_foundry": {"endpoint": ..., "key": ...}}.
    # Merged into the stored config; secret fields are accepted here but never
    # echoed back. An empty-string value clears that field.
    llm_providers: dict[str, dict[str, str]] | None = None
    github_repo: str | None = None
    issue_context_ttl_minutes: int | None = None
    # When true, the per-conversation stats sidebar is rendered in the UI.
    # Token usage is collected and persisted regardless.
    show_chat_stats: bool | None = None
    # When true, the SPA shows a browser notification on turn completion while
    # the window is unfocused (requires the user to grant browser permission).
    notifications_enabled: bool | None = None
    # Max tool-call iterations per user turn before the stream aborts.
    max_tool_rounds: int | None = None
    # Map of MCP server name -> enabled.    mcp_enabled: dict[str, bool] | None = None
    # Map of MCP server name -> attachment config (per-topic attachments are
    # stored separately, this map is the global registry of available servers).
    mcp_servers: dict[str, dict[str, Any]] | None = None
    # Stored as opaque tokens; the backend never returns these in responses.
    api_keys: dict[str, str] | None = None
    # App-level switch for the GitHub-issue association feature.
    issue_associations_enabled: bool | None = None
    # Azure AI Speech for the speech-to-text composer (key goes via api_keys as
    # `azure_speech_key`). Endpoint is the resource URL; language is BCP-47.
    azure_speech_endpoint: str | None = None
    azure_speech_language: str | None = None
    # Live meeting assistant: enablement + model + reasoning effort (fast
    # analysis / Q&A).
    live_enabled: bool | None = None
    live_fast_model: str | None = None
    live_reasoning_effort: str | None = None
    # Map of Precursor capability section -> exposed over the built-in MCP server.
    mcp_expose: dict[str, bool] | None = None
    # Serve the built-in 'precursor' MCP server over HTTP (localhost) too.
    mcp_http_enabled: bool | None = None
    # --- System settings (env default + DB override) ---
    # Prompt budgeting.
    llm_max_input_tokens: int | None = None
    llm_max_tool_result_tokens: int | None = None
    # Scheduler (only the live-applicable timeout is editable).
    scheduled_run_timeout_seconds: int | None = None
    # Tool-result retention window in days (0 = keep forever / disabled).
    tool_result_retention_days: int | None = None
    # Live transcript retention window in days (0 = keep forever). Deletes only
    # transcript segments of ended sessions; insights/notes/summary are kept.
    live_transcript_retention_days: int | None = None
    # Command runner ("jail").
    cmd_runner_jail: bool | None = None
    cmd_runner_image: str | None = None
    cmd_runner_network: bool | None = None
    cmd_runner_timeout_seconds: int | None = None
    cmd_runner_max_output_bytes: int | None = None
    cmd_runner_memory: str | None = None
    cmd_runner_pids_limit: int | None = None
    cmd_runner_cpus: str | None = None
    # Agents mode (Copilot SDK). Opt-in; download/runtime gated by availability.
    agents_enabled: bool | None = None
    agents_default_model: str | None = None
    agents_reasoning_effort: str | None = None
    agents_context_tier: str | None = None
    agents_approval_policy: str | None = None
    agents_system_prompt: str | None = None
    agents_watchdog_timeout_seconds: int | None = None
    # Folder backup (services/backup.py) — copy the DB + blob store into a
    # user-picked directory (e.g. a OneDrive-synced folder) on a daily cadence.
    backup_enabled: bool | None = None
    backup_dir: str | None = None
    backup_retention: int | None = None


class SettingsRead(BaseModel):
    theme: Theme = "system"
    llm_model: str = "claude-sonnet-4.5"
    # "" => auto/off (no reasoning_effort sent); otherwise low|medium|high.
    llm_reasoning_effort: str = ""
    github_repo: str = ""
    issue_context_ttl_minutes: int = 60
    show_chat_stats: bool = True
    notifications_enabled: bool = False
    max_tool_rounds: int = 15
    mcp_enabled: dict[str, bool] = Field(default_factory=dict)
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Booleans only — actual values are write-only and never echoed.
    api_keys_present: dict[str, bool] = Field(default_factory=dict)
    # Where the effective GitHub token comes from. Lets the UI hide the token
    # input when the user is already signed in via `gh auth login`.
    github_token_source: GitHubTokenSource = "none"
    issue_associations_enabled: bool = True
    # Active LLM provider id + per-provider public config (secrets redacted) and
    # a per-provider secret-presence map.
    llm_provider: str = "github_copilot"
    llm_providers: dict[str, dict[str, str]] = Field(default_factory=dict)
    llm_providers_present: dict[str, dict[str, bool]] = Field(default_factory=dict)
    # Azure AI Speech: configured endpoint + language (never echoes the key) and
    # a readiness flag the composer uses to choose the STT provider.
    azure_speech_endpoint: str = ""
    azure_speech_language: str = ""
    stt_azure_ready: bool = False
    # Live meeting assistant: enablement + model + reasoning effort (fast
    # analysis / Q&A). Empty model resolves to the default chat model.
    live_enabled: bool = True
    live_fast_model: str = ""
    live_reasoning_effort: str = ""
    # Which Precursor capability sections the built-in MCP server exposes.
    mcp_expose: dict[str, bool] = Field(default_factory=dict)
    # HTTP transport for the built-in 'precursor' MCP server.
    mcp_http_enabled: bool = False
    # Effective localhost endpoint URL, or null when the app isn't loopback-bound.
    mcp_http_url: str | None = None
    # True when the app is bound to a loopback host (HTTP transport is allowed).
    mcp_http_loopback_ok: bool = True
    # --- System settings (effective: env default with DB override applied) ---
    llm_max_input_tokens: int = 600_000
    llm_max_tool_result_tokens: int = 20_000
    scheduled_run_timeout_seconds: int = 600
    tool_result_retention_days: int = 0
    live_transcript_retention_days: int = 7
    cmd_runner_jail: bool = True
    cmd_runner_image: str = "python:3.14-slim"
    cmd_runner_network: bool = False
    cmd_runner_timeout_seconds: int = 120
    cmd_runner_max_output_bytes: int = 100_000
    cmd_runner_memory: str = "512m"
    cmd_runner_pids_limit: int = 256
    cmd_runner_cpus: str = "1"
    # True when Docker is usable right now (informs the jail toggle in the UI).
    docker_available: bool = False
    # Agents mode (Copilot SDK): the enabled preference, whether the runtime is
    # usable right now, and the default model for new agent sessions.
    agents_enabled: bool = False
    agents_available: bool = False
    agents_unavailable_reason: str | None = None
    agents_default_model: str = "claude-sonnet-4.5"
    # Reasoning effort + context tier applied to new agent sessions. "" effort
    # and "default" tier leave the SDK defaults unchanged.
    agents_reasoning_effort: str = ""
    agents_context_tier: str = "default"
    # Default approval policy for agent actions: manual | balanced | autonomous.
    agents_approval_policy: str = "balanced"
    # Extra system-message preamble appended to every agent session.
    agents_system_prompt: str = ""
    # Minutes... seconds, actually: how long a running session may sit with no
    # new runtime events before the watchdog marks it interrupted (resumable).
    agents_watchdog_timeout_seconds: int = 600
    # Folder backup (services/backup.py): the enabled preference, target folder,
    # snapshot retention, and read-only last-run state for the UI.
    backup_enabled: bool = False
    backup_dir: str = ""
    backup_retention: int = 7
    backup_last_run_at: str | None = None
    # "ok" | "error" | null (never run).
    backup_last_status: str | None = None
    backup_last_error: str | None = None
