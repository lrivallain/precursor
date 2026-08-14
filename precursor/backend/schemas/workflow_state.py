"""Workflow state schemas — the pipeline's own durable key/value memory."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from precursor.backend.models.workflow_state import (
    WORKFLOW_STATE_MAX_KEY,
    WORKFLOW_STATE_MAX_VALUE,
)

# Same grammar as an agent-state key: stable, unambiguous, namespaceable — and
# safe to embed in a ``{{state.<key>}}`` placeholder without quoting.
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _normalize_key(v: str) -> str:
    v = v.strip().lower()
    if not _KEY_RE.match(v):
        raise ValueError(
            "key must start with a letter or digit and only contain lowercase "
            "letters, digits, dots, hyphens or underscores"
        )
    return v


class WorkflowStateWrite(BaseModel):
    """Upsert payload — the value replaces any existing entry for ``key``."""

    key: str = Field(min_length=1, max_length=WORKFLOW_STATE_MAX_KEY)
    value: str = Field(default="", max_length=WORKFLOW_STATE_MAX_VALUE)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        return _normalize_key(v)


class WorkflowStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_id: int
    key: str
    value: str
    created_at: datetime
    updated_at: datetime


class WorkflowStateSummary(BaseModel):
    """Key index entry — everything *but* the body."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    size: int
    updated_at: datetime
