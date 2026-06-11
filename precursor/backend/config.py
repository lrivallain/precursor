"""Application settings sourced from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
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
    cors_origins: list[str] = Field(default_factory=list)

    # Database
    database_url: str = "sqlite+aiosqlite:///./precursor.db"

    # LLM
    llm_provider: Literal["github_models", "mock"] = "github_models"
    llm_model: str = "openai/gpt-4o-mini"
    # GitHub Models PAT is read from GITHUB_TOKEN (unprefixed) to match the
    # convention used by Actions and the `gh` CLI.
    github_token: str = Field(default="", validation_alias="GITHUB_TOKEN")

    # GitHub repo reference (owner/name), overridable from the UI settings.
    github_repo: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
