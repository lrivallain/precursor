"""Agent state schemas — an agent's private cross-run scratchpad entries."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from precursor.backend.models.agent_state import (
    AGENT_STATE_MAX_KEY,
    AGENT_STATE_MAX_VALUE,
)

# Keys are handles the agent invents and reuses across runs, so they have to be
# stable and unambiguous: lowercase, no whitespace, dots/hyphens/underscores for
# namespacing (e.g. "inbox.last_seen_id").
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _normalize_key(v: str) -> str:
    v = v.strip().lower()
    if not _KEY_RE.match(v):
        raise ValueError(
            "key must start with a letter or digit and only contain lowercase "
            "letters, digits, dots, hyphens or underscores"
        )
    return v


class AgentStateWrite(BaseModel):
    """Upsert payload — the value replaces any existing entry for ``key``."""

    key: str = Field(min_length=1, max_length=AGENT_STATE_MAX_KEY)
    value: str = Field(default="", max_length=AGENT_STATE_MAX_VALUE)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        return _normalize_key(v)


class AgentStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    key: str
    value: str
    created_at: datetime
    updated_at: datetime


class AgentStateSummary(BaseModel):
    """Key index entry — everything *but* the body.

    Used by the prompt injection and by list views that must not pay for (or
    disclose) the full value.
    """

    model_config = ConfigDict(from_attributes=True)

    key: str
    size: int
    updated_at: datetime
