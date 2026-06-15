"""Memory schemas — long-term notes injected into chat system prompts."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _normalize_kind(v: str) -> str:
    v = v.strip().lower()
    if not _KIND_RE.match(v):
        raise ValueError(
            "kind must start with a letter and only contain lowercase letters, digits, or hyphens"
        )
    return v


class MemoryBase(BaseModel):
    kind: str = Field(default="context", min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=4_000)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        return _normalize_kind(v)


class MemoryCreate(MemoryBase):
    pass


class MemoryUpdate(BaseModel):
    kind: str | None = Field(default=None, min_length=1, max_length=32)
    content: str | None = Field(default=None, min_length=1, max_length=4_000)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        return _normalize_kind(v) if v is not None else None


class MemoryRead(MemoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
