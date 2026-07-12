"""Live meeting assistant Pydantic schemas.

Mirrored on the frontend in ``frontend/src/lib/types.ts``. Read models never
embed secrets; segment/insight reads carry only presentation fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    notes: str | None = Field(default=None, max_length=100000)
    features: list[str] | None = None


class MeetingSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    status: MeetingStatus
    language: str | None = None
    topic_id: int | None = None
    chat_id: int | None = None
    speaker_names: dict[str, str] = Field(default_factory=dict)
    attendees: list[str] = Field(default_factory=list)
    context_notes: list[str] = Field(default_factory=list)
    notes: str = ""
    features: list[str] = Field(default_factory=list)
    external_meeting: dict[str, Any] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SpeakerRenameRequest(BaseModel):
    # The raw diarization label to rename (e.g. "Guest-2").
    label: str = Field(min_length=1, max_length=64)
    # The chosen display name. Empty (or equal to the label) clears the mapping.
    name: str = Field(default="", max_length=64)


class AttendeesUpdate(BaseModel):
    attendees: list[str] = Field(default_factory=list)


class ContextNoteAdd(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ContextNotesUpdate(BaseModel):
    notes: list[str] = Field(default_factory=list)


class MeetingAttachmentRead(BaseModel):
    id: int
    mime: str
    original_filename: str
    url: str
    is_image: bool


class TranslateRequest(BaseModel):
    target_lang: str = Field(min_length=2, max_length=32)
    # When given, translate these spoken-text lines independently (a batch of new
    # transcript segments), returning one translation per line. Otherwise the
    # whole current transcript is translated as a single block.
    texts: list[str] | None = Field(default=None)


class TranslateResult(BaseModel):
    text: str
    lines: list[str] = Field(default_factory=list)
    target_lang: str
    model: str


class SuggestResult(BaseModel):
    # True only when the model judges there's something worth helping with now.
    has_suggestion: bool = False
    suggestion: str = ""
    model: str


class MeetingSegmentCreate(BaseModel):
    text: str = Field(min_length=1)
    # Diarization label from Azure ConversationTranscriber (e.g. "Guest-1").
    speaker_label: str | None = Field(default=None, max_length=64)
    # Milliseconds from the session's recording start.
    offset_ms: int | None = Field(default=None, ge=0)


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


class MeetingAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class MeetingSummaryResult(BaseModel):
    summary: str
    model: str


class MeetingSummaryPost(BaseModel):
    # The (possibly user-edited) markdown to append to the linked topic.
    summary: str = Field(min_length=1)


class MeetingSummaryPostResult(BaseModel):
    topic_id: int
    message_id: int


class AgendaAttendee(BaseModel):
    name: str
    email: str | None = None


class AgendaEvent(BaseModel):
    id: str | None = None
    subject: str
    start: str | None = None
    end: str | None = None
    organizer: str | None = None
    attendees: list[AgendaAttendee] = Field(default_factory=list)
    is_online: bool = False
    body: str | None = None
    body_preview: str | None = None


class AgendaResponse(BaseModel):
    available: bool
    events: list[AgendaEvent] = Field(default_factory=list)
    detail: str | None = None


class LinkMeetingRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    start: str | None = None
    end: str | None = None
    organizer: str | None = None
    attendees: list[AgendaAttendee] = Field(default_factory=list)
    is_online: bool = False
    body: str | None = None
    body_preview: str | None = None


class TopicSummaryResult(BaseModel):
    summary: str
    model: str
