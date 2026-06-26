"""Skill-related Pydantic schemas.

Skills are addressed by ``name`` (the on-disk folder / slash-command name).
Content lives in shared SKILL.md files; the read model merges that with the
DB enablement state. See ``services/skills.py``.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# File-backed skills are authored by other tools too, so their description and
# body can be far larger than the old DB-column limits implied. These caps only
# guard against pathological input; real Copilot CLI skills sit well under them
# (descriptions ~1 KB, instruction bodies up to a few tens of KB).
MAX_DESCRIPTION_LEN = 4_000
MAX_INSTRUCTIONS_LEN = 500_000


def _validate_name(v: str) -> str:
    v = v.strip().lower()
    if not _NAME_RE.match(v):
        raise ValueError(
            "name must start with a letter and only contain lowercase letters, digits, or hyphens"
        )
    return v


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS_LEN)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        return _validate_name(v)


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    instructions: str | None = Field(default=None, max_length=MAX_INSTRUCTIONS_LEN)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_name(v)


class SkillRead(BaseModel):
    name: str
    description: str | None = None
    instructions: str = ""
    # Active = usable as a slash command (file-backed + enabled, or legacy).
    enabled: bool
    active: bool
    # Legacy skills still carry their content in the DB and expose "Migrate".
    legacy: bool
