"""Skill-related Pydantic schemas."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class SkillBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    instructions: str = Field(default="", max_length=20_000)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must start with a letter and only contain lowercase letters, digits, or hyphens"
            )
        return v


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    instructions: str | None = Field(default=None, max_length=20_000)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must start with a letter and only contain lowercase letters, digits, or hyphens"
            )
        return v


class SkillRead(SkillBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
