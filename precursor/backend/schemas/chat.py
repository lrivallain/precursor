"""Chat-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.schemas.schedule import UtcDateTime


class ChatBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    description_as_system_prompt: bool = False
    pinned: bool = False
    role_id: int | None = None


class ChatCreate(ChatBase):
    # Optional explicit slug. If omitted, the server derives one from the title.
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    # Set by the client when ``title`` is a placeholder it picked ("New chat")
    # rather than something the user typed, licensing the server to replace it
    # with one derived from the first message.
    autoname: bool = False


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
    # Side chat link (see services/side_chats.py). The title is resolved
    # server-side so the chat list can show its "from <topic>" chip.
    parent_topic_id: int | None = None
    parent_topic_title: str | None = None
    parent_message_id: int | None = None
    seed_content: str | None = None


class SideChatCreate(BaseModel):
    """Start a side chat from a topic, optionally from one of its replies."""

    message_id: int | None = None


class SideChatReminder(BaseModel):
    remind_at: UtcDateTime
    status: str


class SideChatItem(BaseModel):
    """One side chat as listed in its parent topic's right panel."""

    id: int
    slug: str
    title: str
    parent_message_id: int | None = None
    from_reply: bool = False
    unread_count: int = 0
    message_count: int = 0
    last_message_at: UtcDateTime | None = None
    created_at: UtcDateTime
    reminder: SideChatReminder | None = None
