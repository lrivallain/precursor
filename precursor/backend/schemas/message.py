"""Message-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.models.message import MessageRole


class AttachmentRead(BaseModel):
    """Image attached to a user message (currently the only supported kind)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int
    message_id: int | None = None
    mime: str
    size: int
    original_filename: str
    created_at: datetime

    @property
    def url(self) -> str:  # pragma: no cover — convenience for clients
        return f"/api/attachments/{self.id}"


class MessageCreate(BaseModel):
    role: MessageRole = MessageRole.USER
    content: str = Field(min_length=1)


class StoppedTurn(BaseModel):
    """Partial assistant reply the user interrupted, to persist as-is."""

    content: str = Field(min_length=1)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int | None = None
    chat_id: int | None = None
    role: MessageRole
    content: str
    tool_calls: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    created_at: datetime
    attachments: list[AttachmentRead] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """Payload for POST /api/topics/{id}/chat — a new user turn to stream."""

    content: str = Field(min_length=1)
    model: str | None = None
    # When set, the persisted/displayed user message stays `content` but the
    # LLM receives `prompt_override` as the last user turn (used by skills).
    prompt_override: str | None = None
    # IDs of previously uploaded attachments (POST /api/topics/{id}/attachments)
    # to bind to this user turn. Ignored when empty.
    attachment_ids: list[int] = Field(default_factory=list)
