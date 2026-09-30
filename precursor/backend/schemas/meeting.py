"""Live meeting assistant Pydantic schemas.

Mirrored on the frontend in ``frontend/src/lib/types.ts``. Read models never
embed secrets; segment/insight reads carry only presentation fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from precursor.backend.schemas.definitions_api import DefinitionIssue, DefinitionsWorkspaceRef

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
    role_id: int | None = None
    status: MeetingStatus | None = None
    notes: str | None = Field(default=None, max_length=100000)
    # Hand-edits to the generated recap, autosaved from the Summary tab so they
    # survive a reload even when the recap is never posted to a topic.
    summary: str | None = Field(default=None, max_length=100000)
    features: list[str] | None = None


class MeetingSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    status: MeetingStatus
    language: str | None = None
    topic_id: int | None = None
    role_id: int | None = None
    chat_id: int | None = None
    speaker_names: dict[str, str] = Field(default_factory=dict)
    attendees: list[str] = Field(default_factory=list)
    context_notes: list[str] = Field(default_factory=list)
    notes: str = ""
    features: list[str] = Field(default_factory=list)
    external_meeting: dict[str, Any] | None = None
    topic_summary: str | None = None
    summary: str | None = None
    summary_posted_at: datetime | None = None
    summary_posted_topic_id: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    archived_at: datetime | None = None
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


class MeetingSegmentUpdate(BaseModel):
    # Corrected spoken text — lets a user fix mistaken words in a phrase.
    text: str = Field(min_length=1)


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


class MeetingAnalyzeResult(BaseModel):
    """The result of one unified analysis pass: the insight snapshot plus an
    optional proactive suggestion (empty when no help is warranted)."""

    insights: list[MeetingInsightRead] = Field(default_factory=list)
    suggestion: str = ""


class MeetingAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class MeetingSummaryRequest(BaseModel):
    """How to write a recap. Omitted, each falls back to the one last used."""

    # A summary template's id (``GET /api/live/summary-templates``).
    template: str | None = Field(default=None, min_length=1, max_length=64)
    # A BCP-47 tag (``fr`` or ``fr-FR``); ``""`` writes in the session's language.
    language: str | None = Field(default=None, max_length=35)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        from precursor.backend.services.meeting_analysis import is_known_language

        if value and not is_known_language(value):
            raise ValueError(f"unsupported summary language '{value}'")
        return value


class MeetingSummaryResult(BaseModel):
    summary: str
    model: str
    # The template and language it was written with ("" = the session's).
    template: str = ""
    language: str = ""


class SummaryTemplateRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    source: Literal["builtin", "file"]
    # Relative to the definitions folder, for a template read from a file.
    path: str | None = None
    # A file that replaces the built-in template of the same id.
    overrides_builtin: bool = False


class SummaryLanguage(BaseModel):
    code: str
    name: str


class SummaryTemplateCatalog(BaseModel):
    templates: list[SummaryTemplateRead]
    # Template files left out because of errors (or ids used twice).
    problems: list[DefinitionIssue] = Field(default_factory=list)
    languages: list[SummaryLanguage] = Field(default_factory=list)
    # What the last recap was written with: the picker starts there.
    last_template: str
    last_language: str = ""
    # The definitions folder, and the workspace showing it in Files (if any).
    folder: str
    workspace: DefinitionsWorkspaceRef | None = None


class SummaryTemplateSelection(BaseModel):
    template: str = Field(min_length=1, max_length=64)
    # ``""`` writes in the session's language.
    language: str = Field(default="", max_length=35)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        from precursor.backend.services.meeting_analysis import is_known_language

        if value and not is_known_language(value):
            raise ValueError(f"unsupported summary language '{value}'")
        return value


class SummaryTemplateFileRequest(BaseModel):
    template: str = Field(min_length=1, max_length=64)
    # Save it as a new template (new id) instead of opening/customizing it.
    duplicate: bool = False


class SummaryTemplateFileResult(BaseModel):
    # The template the file declares: a new id for a duplicate.
    id: str
    # Relative to the definitions folder.
    path: str
    # False when the template already had its file.
    created: bool
    folder: str
    workspace: DefinitionsWorkspaceRef | None = None
    # The same file, relative to the workspace's Files root, to open it there.
    workspace_path: str | None = None


class MeetingSummaryPost(BaseModel):
    # The (possibly user-edited) markdown to append to the linked topic.
    summary: str = Field(min_length=1)


class MeetingPostResult(BaseModel):
    topic_id: int
    message_id: int


class MeetingSummaryPostResult(MeetingPostResult):
    posted_at: datetime
    # Set when the linked topic also carries a GitHub issue and the summary was
    # mirrored there as a comment; null when no issue is attached or the mirror
    # failed/was skipped.
    issue_number: int | None = None
    issue_comment_url: str | None = None


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
    # Teams join URL, used to locate the meeting's transcript later.
    join_url: str | None = None
    body_preview: str | None = None


class AgendaResponse(BaseModel):
    available: bool
    events: list[AgendaEvent] = Field(default_factory=list)
    detail: str | None = None


class LinkMeetingRequest(BaseModel):
    # Graph event id + Teams join URL are carried through so the summary can be
    # rebuilt from the meeting's Teams transcript ("no local record" path).
    id: str | None = None
    subject: str = Field(min_length=1, max_length=500)
    start: str | None = None
    end: str | None = None
    organizer: str | None = None
    attendees: list[AgendaAttendee] = Field(default_factory=list)
    is_online: bool = False
    join_url: str | None = Field(default=None, max_length=2000)
    body: str | None = None
    body_preview: str | None = None


class MeetingTranscriptPart(BaseModel):
    """One Teams transcription session ("Partie 1", "Partie 2", …).

    ``ended_at`` is absent on older transcripts, so the UI must render a part
    without an end time.
    """

    id: str
    created_at: str | None = None
    ended_at: str | None = None


class MeetingTranscriptListResult(BaseModel):
    available: bool
    parts: list[MeetingTranscriptPart] = Field(default_factory=list)
    detail: str | None = None


class MeetingTranscriptSummaryRequest(MeetingSummaryRequest):
    # Which transcription session(s) to summarise. Empty means "the most recent
    # one" — the UI only sends ids once the user has picked from several.
    transcript_ids: list[str] = Field(default_factory=list)


class MeetingTranscriptSummaryResult(MeetingSummaryResult):
    """A summary generated from the linked Teams meeting transcript."""

    # Echo back which transcription sessions fed the recap.
    transcript_ids: list[str] = Field(default_factory=list)


class TopicSummaryResult(BaseModel):
    summary: str
    model: str
