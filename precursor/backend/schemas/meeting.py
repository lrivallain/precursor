"""Live meeting assistant Pydantic schemas.

Mirrored on the frontend in ``frontend/src/lib/types.ts``. Read models never
embed secrets; segment/insight reads carry only presentation fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InsightKind = Literal["action_item", "decision", "question", "suggestion", "risk", "note"]
MeetingStatus = Literal["active", "ended"]


class MeetingSessionCreate(BaseModel):
    # Optional — the server generates a dated default title when omitted.
    title: str | None = Field(default=None, max_length=255)
    # BCP-47 tag; null resolves to the configured Azure Speech language.
    language: str | None = Field(default=None, max_length=32)
    # Optional topic whose context seeds the assistant.
    topic_id: int | None = None
    # Optional explicit slug; derived from the title when omitted.
    slug: str | None = Field(default=None, min_length=1, max_length=255)


class MeetingSessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=32)
    topic_id: int | None = None
    status: MeetingStatus | None = None


class MeetingSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    status: MeetingStatus
    language: str | None = None
    topic_id: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MeetingSegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    speaker_label: str | None = None
    text: str
    offset_ms: int | None = None
    created_at: datetime


class MeetingInsightRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    kind: InsightKind
    content: str
    created_at: datetime
