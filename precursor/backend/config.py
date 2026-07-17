"""Application settings sourced from environment variables / .env."""

from __future__ import annotations

import os
from functools import cached_property, lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PRECURSOR_",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"
    # Seconds uvicorn waits for in-flight requests (e.g. long-lived SSE chat
    # streams) to finish on shutdown before force-closing them. Kept small so
    # Ctrl-C releases the listening port promptly instead of hanging on an open
    # stream and leaving the port unusable for a TIME_WAIT window.
    shutdown_grace_seconds: int = 3
    # Comma-separated string in env (pydantic-settings JSON-decodes list fields
    # too eagerly to accept a bare value). Parsed via `cors_origins` below.
    cors_origins_raw: str = Field(default="", validation_alias="PRECURSOR_CORS_ORIGINS")

    @cached_property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]

    # Database
    database_url: str = "sqlite+aiosqlite:///./precursor.db"

    # On-disk data directory for working copies (e.g. Workspace git
    # clones). Relative paths resolve against the process working directory.
    data_dir: str = ".precursor"

    @cached_property
    def workspaces_dir(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "workspaces")

    # Attachment bytes live on disk as content-addressed files here (keyed by
    # SHA-256, sharded two levels deep) rather than as BLOBs in the DB, so the
    # database file stays small and cheap to back up / copy.
    @cached_property
    def blobs_dir(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "blobs")

    # Skills live as ``<copilot_home>/skills/<name>/SKILL.md`` files shared with
    # the GitHub Copilot CLI and other tools. An explicit override (handy for
    # tests / non-standard setups) wins; otherwise we resolve the Copilot home
    # the same way the CLI does: COPILOT_HOME → XDG_CONFIG_HOME/copilot → ~/.copilot.
    skills_dir_override: str = Field(default="", validation_alias="PRECURSOR_SKILLS_DIR")

    @cached_property
    def skills_dir(self) -> str:
        if self.skills_dir_override.strip():
            return str(Path(self.skills_dir_override).expanduser().resolve())
        copilot_home = os.environ.get("COPILOT_HOME", "").strip()
        if copilot_home:
            base = Path(copilot_home)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
            base = Path(xdg) / "copilot" if xdg else Path.home() / ".copilot"
        return str((base / "skills").expanduser())

    # LLM — the active provider and its credentials live in the app settings
    # (Settings → Model), not in the environment, so they can be changed at
    # runtime without a restart. See services/llm/registry.py.

    # Prompt budgeting — caps how much of the (history + tool results) transcript
    # is sent to the model, so a few large file reads / fetches across tool
    # rounds can't overflow the context window. Lower these for smaller models.
    llm_max_input_tokens: int = 600_000
    llm_max_tool_result_tokens: int = 20_000

    # Scheduler — drives recurring "scheduled" topics. Single in-process ticker
    # + a small worker pool; see services/scheduler.py.
    scheduler_enabled: bool = True
    scheduler_poll_seconds: int = 30
    scheduler_concurrency: int = 2
    scheduled_run_timeout_seconds: int = 600
    # One-shot reminders (services/reminder_ticker.py) — how often to poll for
    # due reminders. Gated by the same ``scheduler_enabled`` flag.
    reminder_poll_seconds: int = 30

    # Tool-result retention (services/tool_result_retention.py) — how long full
    # TOOL-result content is kept before its ``content`` is replaced in place
    # with a short placeholder to bound DB growth. 0 = disabled / keep forever.
    # The sweep runs on startup and periodically via a ticker gated by
    # ``scheduler_enabled`` (poll interval below, default daily).
    tool_result_retention_days: int = 0
    tool_result_retention_poll_seconds: int = 86_400

    # WorkIQ token keep-alive (services/mcp/workiq_keepalive.py) — a background
    # ticker that silently refreshes the WorkIQ preview OAuth token before it
    # expires, so the hosted session survives without frequent interactive
    # re-sign-in. Only does work while preview is on and tokens exist.
    workiq_keepalive_enabled: bool = True
    workiq_keepalive_poll_seconds: int = 60
    # Refresh once the access token is within this many seconds of expiring.
    workiq_keepalive_refresh_margin_seconds: int = 300

    # WorkIQ interactive re-auth UX (services/mcp/workiq_preview.py). We always
    # pre-fill the Entra account picker with the last signed-in user
    # (``login_hint``) so re-auth skips account selection. When this is on we
    # additionally attempt a non-interactive ``prompt=none`` authorization first:
    # it completes with zero clicks if the browser still holds a live Entra SSO
    # session, and only falls back to the visible prompt when Entra reports
    # interaction is required. Turn off to always show the interactive prompt.
    workiq_silent_reauth_enabled: bool = True

    # Command runner (cmd-runner MCP) — runs bash/python/node either inside a
    # throwaway Docker "jail" (default) or, when the jail is disabled, directly
    # on the host with full local disk access. See services/cmd_runner.py.
    cmd_runner_jail: bool = True
    cmd_runner_image: str = "python:3.14-slim"
    cmd_runner_network: bool = False
    cmd_runner_timeout_seconds: int = 120
    cmd_runner_max_output_bytes: int = 100_000
    cmd_runner_memory: str = "512m"
    cmd_runner_pids_limit: int = 256
    cmd_runner_cpus: str = "1"

    # Cockpits — user-registered local webapps launched on demand. Each cockpit
    # runs its command directly on the host (same trust level as cmd-runner host
    # mode) and is embedded via the backend reverse proxy. Because this is
    # arbitrary local command execution, it is only ever exposed on a loopback
    # bind. See services/cockpits.py.
    cockpits_enabled: bool = True
    # How long to wait for a started cockpit's port to accept connections before
    # marking it "unreachable" (the process keeps running so the user can retry
    # or open it in a tab).
    cockpits_readiness_timeout_seconds: int = 40
    # Grace period between SIGTERM and SIGKILL when stopping a cockpit tree.
    cockpits_stop_grace_seconds: int = 5
    # Max bytes of combined stdout/stderr retained per running cockpit (ring).
    cockpits_max_log_bytes: int = 200_000

    # MCP client — the chat/tool loop keeps each enabled server's session warm
    # across turns instead of re-spawning/initialising it every message (that
    # cold start dominated time-to-first-token). A warm session is released
    # after this many seconds with no tool calls; set to 0 to disable pooling
    # and open a fresh session per turn.
    mcp_idle_ttl_seconds: int = 600
    # GitHub MCP (remote) advertises one tool group per toolset. Requesting
    # "all" floods the prompt with hundreds of tools, slowing the first token.
    # Comma-separated list sent as the ``X-MCP-Toolsets`` header; use "all" to
    # restore the full catalogue.
    github_mcp_toolsets: str = "context,repos,issues,pull_requests,users"

    @cached_property
    def cmd_runner_scratch_dir(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "cmd-runner" / "scratch")

    # Agents mode (opt-in) — long-running Copilot SDK agent sessions. Disabled
    # by default at the env level; the effective on/off lives in the DB app
    # settings (Settings → Agents) so it can be toggled at runtime. The SDK
    # persists each session's state under ``agents_home`` (its ``COPILOT_HOME``).
    agents_enabled: bool = False
    # Model used for new agent sessions when the caller doesn't specify one.
    agents_default_model: str = "claude-sonnet-4.5"
    # Default approval policy gating an agent's actions. One of:
    #   "manual"     — ask before every action (most cautious)
    #   "balanced"   — auto-approve read-only actions, ask for writes/shell/etc.
    #   "autonomous" — auto-approve everything (no prompts)
    agents_approval_policy: str = "balanced"
    # Extra system-message preamble appended to every agent session, on top of
    # the SDK's base prompt (which we cannot override) and any topic binding.
    # Empty by default; editable at runtime via Settings → Agents.
    agents_system_prompt: str = ""
    # How long (seconds) a running agent session may go without any new runtime
    # event before the watchdog marks it ``interrupted`` (resumable). Guards
    # against a stuck/runaway turn pinning a session in "running" forever.
    agents_watchdog_timeout_seconds: int = 600

    @cached_property
    def agents_home(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "agents" / "copilot-home")

    # Backup (services/backup.py) — periodic copy of the SQLite DB + blob store
    # into a plain folder the user picks (e.g. a OneDrive-synced directory).
    # Disabled by default at the env level; the effective on/off, target dir and
    # retention live in the DB app settings (Settings → Backup) so they can be
    # changed at runtime. The ticker polls on ``backup_poll_seconds`` and runs a
    # backup once ``backup_interval_seconds`` has elapsed since the last success.
    backup_enabled: bool = False
    backup_dir: str = ""
    backup_interval_seconds: int = 86_400
    backup_retention: int = 7
    backup_poll_seconds: int = 3_600


@lru_cache
def get_settings() -> Settings:
    return Settings()
