"""Application settings sourced from environment variables / .env."""

from __future__ import annotations

from functools import cached_property, lru_cache
from typing import Literal

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

    # LLM
    llm_provider: Literal["github_copilot", "github_models", "mock"] = "github_copilot"
    # GitHub Models / Copilot token — read from GITHUB_TOKEN (unprefixed) to
    # match the convention used by Actions and the `gh` CLI.
    github_token: str = Field(default="", validation_alias="GITHUB_TOKEN")

    # Azure AI Speech (optional) — powers speech-to-text in the chat composer.
    # Key + endpoint are read unprefixed (AZURE_SPEECH_*) to match the Azure CLI
    # / SDK convention; both can also be set from the Settings panel (DB
    # override). The endpoint is the resource URL, e.g.
    # https://<name>.cognitiveservices.azure.com/.
    azure_speech_key: str = Field(default="", validation_alias="AZURE_SPEECH_KEY")
    azure_speech_endpoint: str = Field(default="", validation_alias="AZURE_SPEECH_ENDPOINT")

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

    @cached_property
    def cmd_runner_scratch_dir(self) -> str:
        from pathlib import Path

        return str(Path(self.data_dir).resolve() / "cmd-runner" / "scratch")


@lru_cache
def get_settings() -> Settings:
    return Settings()
