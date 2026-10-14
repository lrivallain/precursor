"""Role-related Pydantic schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RoleBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    system_prompt: str = Field(default="", max_length=20_000)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    # Accepted so a client can send the whole role back; a different name is
    # refused, as everything refers to a role by its name.
    name: str | None = Field(default=None, min_length=1, max_length=64)
    system_prompt: str | None = Field(default=None, max_length=20_000)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class RoleRead(BaseModel):
    """A role as the API shows it. The name is its identity: there is no id."""

    name: str
    system_prompt: str
    is_default: bool
    description: str | None = None
    # Relative to the definitions folder when the role is a file; else null.
    path: str | None = None
