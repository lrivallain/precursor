"""Cockpit Pydantic schemas.

A cockpit is a user-registered local webapp. The persisted *definition* (name,
command, port, …) is separate from the ephemeral *runtime status* (whether the
process is running and reachable), so the read model carries both: the columns
from the DB plus a live ``status`` block filled in by the ``CockpitManager``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

MAX_COMMAND_LEN = 4_000
MAX_ENV_LEN = 8_000

# Lifecycle states surfaced to the UI:
# * stopped      — no process (never started or cleanly stopped)
# * starting     — spawned, port not yet accepting connections
# * running      — port is reachable; safe to embed
# * unreachable  — process alive but port never opened within the timeout
# * crashed      — process exited before/after becoming ready
CockpitState = Literal["stopped", "starting", "running", "unreachable", "crashed"]


def _validate_env(v: str | None) -> str | None:
    """Ensure ``env`` is a JSON object of string→string, stored as text."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    try:
        parsed = json.loads(v)
    except json.JSONDecodeError as exc:
        raise ValueError("env must be a JSON object") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(val, str) for k, val in parsed.items()
    ):
        raise ValueError("env must be a JSON object mapping string keys to string values")
    return json.dumps(parsed)


class CockpitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    command: str = Field(min_length=1, max_length=MAX_COMMAND_LEN)
    port: int = Field(ge=1, le=65_535)
    description: str | None = None
    cwd: str | None = None
    # JSON object string, e.g. '{"NODE_ENV": "development"}'.
    env: str | None = Field(default=None, max_length=MAX_ENV_LEN)
    # Optional explicit slug; derived from the name when omitted.
    slug: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug must start with a letter/digit and contain only "
                "lowercase letters, digits, or hyphens"
            )
        return v

    @field_validator("env")
    @classmethod
    def _check_env(cls, v: str | None) -> str | None:
        return _validate_env(v)


class CockpitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    command: str | None = Field(default=None, min_length=1, max_length=MAX_COMMAND_LEN)
    port: int | None = Field(default=None, ge=1, le=65_535)
    description: str | None = None
    cwd: str | None = None
    env: str | None = Field(default=None, max_length=MAX_ENV_LEN)

    @field_validator("env")
    @classmethod
    def _check_env(cls, v: str | None) -> str | None:
        return _validate_env(v)


class CockpitStatus(BaseModel):
    """Live runtime state — never persisted; recomputed from the manager."""

    state: CockpitState = "stopped"
    pid: int | None = None
    port: int | None = None
    started_at: datetime | None = None
    # Populated on crashed/unreachable to help the user debug.
    exit_code: int | None = None
    detail: str | None = None


class CockpitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None
    command: str
    cwd: str | None = None
    port: int
    env: str | None = None
    created_at: datetime
    updated_at: datetime
    status: CockpitStatus = Field(default_factory=CockpitStatus)


class CockpitLogs(BaseModel):
    logs: str = ""
