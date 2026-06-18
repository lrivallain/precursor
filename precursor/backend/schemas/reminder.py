"""Reminder schemas — one-shot date/time reminders for topics and chats."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.schedule import UtcDateTime

# Discriminates which kind of container a reminder is attached to.
ContainerKind = Literal["topic", "chat"]


class ReminderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int | None = None
    chat_id: int | None = None
    remind_at: UtcDateTime
    note: str | None = None
    status: str = "scheduled"
    fired_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ReminderCreate(BaseModel):
    """Set (or replace) a reminder on a container."""

    remind_at: UtcDateTime
    note: str | None = Field(default=None, max_length=2000)


class ReminderItem(ReminderRead):
    """A reminder enriched with its container's identity for the sidebar list."""

    container: ContainerKind
    title: str
    slug: str
