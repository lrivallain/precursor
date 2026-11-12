"""Request and response shapes for user-defined MCP servers."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# "none": static headers only. "azure_cli": an Entra bearer token minted by the
# user's Azure CLI sign-in (``az account get-access-token``).
MCPServerAuth = Literal["none", "azure_cli"]

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _normalize_name(v: str) -> str:
    v = v.strip().lower()
    if not _NAME_RE.match(v):
        raise ValueError(
            "name must start with a letter and contain only lowercase letters, "
            "digits, or hyphens (max 64 chars)"
        )
    return v


class UserServerBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    transport: str = Field(pattern=r"^(streamable_http|stdio)$")
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    auth: MCPServerAuth = "none"
    auth_tenant: str | None = None
    auth_resource: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _normalize_name(v)


class UserServerCreate(UserServerBase):
    pass


class UserServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    transport: str | None = Field(default=None, pattern=r"^(streamable_http|stdio)$")
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    headers: dict[str, str] | None = None
    auth: MCPServerAuth | None = None
    auth_tenant: str | None = None
    auth_resource: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        return None if v is None else _normalize_name(v)


class MCPAuthDetectRequest(BaseModel):
    url: str = Field(min_length=1)


class MCPAuthDetection(BaseModel):
    """What an MCP server's endpoint says about the sign-in it expects."""

    # False when the server answered without asking for credentials.
    auth_required: bool
    # The mode Precursor can use for it: ``azure_cli`` when the server
    # advertises an Entra authorization server, else ``none``.
    auth: MCPServerAuth
    # Entra tenant the server's authorization server lives in (empty for the
    # multi-tenant ``organizations``/``common`` authorities).
    tenant: str | None = None
    # Application ID URI to request the token for, derived from the advertised scope.
    resource: str | None = None
    # One line for the editor: what was found, or why detection failed.
    message: str
