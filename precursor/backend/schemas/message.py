"""Message-related Pydantic schemas."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from precursor.backend.models.message import MessageRole


class AttachmentRead(BaseModel):
    """Attachment linked to a user message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int | None = None
    chat_id: int | None = None
    message_id: int | None = None
    mime: str
    size: int
    original_filename: str
    created_at: datetime

    @property
    def url(self) -> str:  # pragma: no cover — convenience for clients
        return f"/api/attachments/{self.id}"


class NoteDraftAttachmentRead(BaseModel):
    """Attachment linked to a note draft before it is published."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    note_draft_id: int
    mime: str
    size: int
    original_filename: str
    created_at: datetime

    @property
    def url(self) -> str:  # pragma: no cover — convenience for clients
        return f"/api/notes/attachments/{self.id}"


class MessageCreate(BaseModel):
    role: MessageRole = MessageRole.USER
    content: str = Field(min_length=1)


class CompactRequest(BaseModel):
    """``/compact`` options: what the summary should focus on, if anything."""

    instructions: str | None = Field(default=None, max_length=4000)


class TurnIndexRead(BaseModel):
    """One turn of the transcript timeline (see ``list_container_turns``)."""

    model_config = ConfigDict(from_attributes=True)

    # The prompt opening the turn, and the last row before the next prompt.
    message_id: int
    last_message_id: int
    created_at: datetime
    prompt: str
    # The first assistant text of the turn, empty while unanswered.
    reply: str
    has_compaction: bool
    agent_session_id: int | None = None


class RewindRequest(BaseModel):
    """Drop the turn opened by ``from_message_id`` (a user prompt) and all later rows.

    ``through_message_id``, when set, is the last row deleted: rows created after
    the user confirmed the rewind are kept.
    """

    from_message_id: int
    through_message_id: int | None = None


class RewindResult(BaseModel):
    deleted: int


class ContextEstimateRead(BaseModel):
    """Estimated history the next turn sends (system prompt and tools excluded)."""

    tokens: int
    by_role: dict[str, int]
    saved_by_hygiene: int
    messages: int
    compacted: bool
    compaction_id: int | None = None
    compacted_messages: int = 0


class StoppedTurn(BaseModel):
    """What a user-stopped turn leaves behind, to persist as-is.

    ``content`` is the partial reply received so far; ``tool_call_ids`` name the
    calls of the latest tool round that never returned, recorded as stopped.
    ``reasoning`` is the model's thinking streamed for that reply, kept with it.
    """

    content: str = ""
    tool_call_ids: list[str] = Field(default_factory=list, max_length=64)
    reasoning: str = ""

    @model_validator(mode="after")
    def _something_to_save(self) -> StoppedTurn:
        if not self.content and not self.tool_call_ids:
            raise ValueError("content or tool_call_ids is required")
        return self


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    topic_id: int | None = None
    chat_id: int | None = None
    role: MessageRole
    content: str
    # Special-row marker; "compaction" = a context-compaction summary.
    kind: str | None = None
    # The model's thinking for this assistant round, shown collapsed.
    reasoning: str | None = None
    tool_calls: str | None = None
    agent_session_id: int | None = None
    # The linked agent's public (UUID) id — used by the UI for deep links and
    # the /agent command so it never has to surface the internal integer id.
    agent_session_public_id: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # The LLM model id that produced this assistant turn, surfaced in the UI.
    model: str | None = None
    # Wall-clock duration in ms the assistant turn took to generate.
    elapsed_ms: int | None = None
    created_at: datetime
    attachments: list[AttachmentRead] = Field(default_factory=list)
    # Follow-up reply chips offered on this assistant turn. Stored as a JSON
    # array string on the ORM model; parsed to a list here for the client.
    suggestions: list[str] = Field(default_factory=list)

    @field_validator("suggestions", mode="before")
    @classmethod
    def _parse_suggestions(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return []
            return [str(s) for s in parsed] if isinstance(parsed, list) else []
        if isinstance(value, list):
            return [str(s) for s in value]
        return []


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
    # IDs of note-draft attachments selected in /notes "add & ask AI".
    note_attachment_ids: list[int] = Field(default_factory=list)
    # Replay an existing user turn whose answer failed: instead of persisting a
    # new user message, reuse this one and drop everything recorded after it
    # (partial answer, tool rows, the error notice). Attachments stay bound to
    # the message, so a retry keeps its images and documents.
    retry_message_id: int | None = None


class NotesRephraseRequest(BaseModel):
    text: str = Field(min_length=1)
    instruction: str | None = None


class NotesRephraseResponse(BaseModel):
    text: str


class SuggestNameResponse(BaseModel):
    """Result of ``/suggest-name``. ``title`` is the newly-applied one.

    Empty when the model returned nothing usable, in which case the existing
    title was left alone and the client says so rather than showing a rename.
    """

    title: str


class NotesAppendRequest(BaseModel):
    text: str = ""
    attachment_ids: list[int] = Field(default_factory=list)


class NotesAppendResponse(BaseModel):
    message: MessageRead


class NotesDraftSaveRequest(BaseModel):
    text: str = ""


class NotesDraftResponse(BaseModel):
    text: str | None
    updated_at: str | None
    attachments: list[NoteDraftAttachmentRead] = Field(default_factory=list)
