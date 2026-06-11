"""Application-settings schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Theme = Literal["light", "dark", "system"]


class SettingsPayload(BaseModel):
    """Partial update — every field is optional."""

    theme: Theme | None = None
    llm_model: str | None = None
    github_repo: str | None = None
    # Map of MCP server name -> enabled.
    mcp_enabled: dict[str, bool] | None = None
    # Map of MCP server name -> attachment config (per-topic attachments are
    # stored separately, this map is the global registry of available servers).
    mcp_servers: dict[str, dict[str, Any]] | None = None
    # Stored as opaque tokens; the backend never returns these in responses.
    api_keys: dict[str, str] | None = None


class SettingsRead(BaseModel):
    theme: Theme = "system"
    llm_model: str = "openai/gpt-4o-mini"
    github_repo: str = ""
    mcp_enabled: dict[str, bool] = Field(default_factory=dict)
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Booleans only — actual values are write-only and never echoed.
    api_keys_present: dict[str, bool] = Field(default_factory=dict)
