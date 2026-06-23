"""Application settings sourced from environment variables / .env."""

from __future__ import annotations

from functools import cached_property, lru_cache

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

    @cached_property
    def agents_home(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "agents" / "copilot-home")


@lru_cache
def get_settings() -> Settings:
    return Settings()
