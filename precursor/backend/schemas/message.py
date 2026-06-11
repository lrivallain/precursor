"""Message-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from precursor.backend.models.message import MessageRole


class MessageCreate(BaseModel):
    role: MessageRole = MessageRole.USER
    content: str = Field(min_length=1)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int
    role: MessageRole
    content: str
    tool_calls: str | None = None
    created_at: datetime


class ChatRequest(BaseModel):
    """Payload for POST /api/topics/{id}/chat — a new user turn to stream."""

    content: str = Field(min_length=1)
    model: str | None = None
