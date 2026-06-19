"""Chat-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    description_as_system_prompt: bool = False
    pinned: bool = False
    role_id: int | None = None


class ChatCreate(ChatBase):
    # Optional explicit slug. If omitted, the server derives one from the title.
    slug: str | None = Field(default=None, min_length=1, max_length=255)


class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    description_as_system_prompt: bool | None = None
    pinned: bool | None = None
    role_id: int | None = None
    # When present, the router normalizes and uniquifies it before storing.
    slug: str | None = Field(default=None, min_length=1, max_length=255)


class ChatRead(ChatBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    last_read_at: datetime | None = None
    unread_count: int = 0  # Computed server-side
