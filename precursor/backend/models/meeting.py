"""Live meeting assistant models — a recorded meeting session.

A ``MeetingSession`` is a top-level entity (like a topic, chat, or workspace):
the user records an ongoing meeting, whose audio is transcribed in the browser
into ``MeetingSegment`` rows, while the backend derives live ``MeetingInsight``
rows (action items, decisions, …) from a rolling window. Raw audio is never
stored — only the transcript and derived insights.

Kept deliberately separate from the topic domain: a session may *reference* a
topic (its context for the assistant), but neither model has to grow to host
the other. The optional ``topic_id`` link uses SET NULL so removing a topic
just detaches it from any sessions that referenced it.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin


class MeetingSession(Base, TimestampMixin):
    __tablename__ = "meeting_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # URL-friendly identifier. Stable across title edits unless changed explicitly.
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # Lifecycle: "active" while resumable (recording or paused), "ended" once
    # finalized. A freshly created session starts "active".
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )

    # BCP-47 language tag for speech recognition (e.g. "en-US" / "fr-FR").
    # Null resolves to the configured Azure Speech language at runtime.
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Optional topic whose context seeds the live assistant. SET NULL on delete
    # so removing the topic just detaches it (the session and its transcript
    # remain intact).
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # When recording first began / when the session was ended. Both null until
    # the respective event; distinct from created_at (row creation).
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # JSON-encoded dict[str, str] mapping a raw diarization label to a
    # user-chosen display name. Labels are namespaced per recording run as
    # "<run>:<label>" (e.g. "2:Guest-1") because Azure re-numbers speakers on
    # every stop/restart — so a rename stays scoped to the run it was made in
    # and never bleeds onto a different voice that reuses the label later.
    # Segments keep their raw label; names apply at display + analysis time.
    speaker_names_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )

    @property
    def speaker_names(self) -> dict[str, str]:
        try:
            data = json.loads(self.speaker_names_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    # JSON-encoded list[str] of attendee display names for the summary. Seeded
    # from speakers confirmed in the transcript (i.e. renamed to a real name);
    # a linked meeting's invitees are only *suggested*, not auto-added. Editable.
    attendees_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )

    @property
    def attendees(self) -> list[str]:
        try:
            data = json.loads(self.attendees_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(x) for x in data]

    # JSON-encoded list[str] of free-form context notes the user pins to the
    # session (e.g. a saved Q&A answer). Injected into the live analysis, Q&A
    # and summary prompts alongside the topic/meeting context.
    context_notes_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )

    @property
    def context_notes(self) -> list[str]:
        try:
            data = json.loads(self.context_notes_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(x) for x in data]

    # Free-form Markdown notes the user takes live during the meeting. Saved as
    # they type (debounced) and when the session is ended.
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    # JSON-encoded dict describing a linked M365 calendar meeting (subject,
    # times, organizer, attendees, is_online), fetched via WorkIQ. Null when
    # none is linked.
    external_meeting_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def external_meeting(self) -> dict[str, object] | None:
        if not self.external_meeting_json:
            return None
        try:
            data = json.loads(self.external_meeting_json)
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    segments: Mapped[list[MeetingSegment]] = relationship(
        "MeetingSegment",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MeetingSegment.created_at, MeetingSegment.id",
    )
    insights: Mapped[list[MeetingInsight]] = relationship(
        "MeetingInsight",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MeetingInsight.created_at, MeetingInsight.id",
    )


class MeetingSegment(Base, TimestampMixin):
    """One finalized transcript phrase. Interim results stay client-side."""

    __tablename__ = "meeting_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("meeting_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Diarization label from Azure ConversationTranscriber (e.g. "Guest-1").
    # Null when diarization is unavailable for a phrase.
    speaker_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Milliseconds from the session's recording start, for stable ordering and
    # timeline display independent of wall-clock created_at.
    offset_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    session: Mapped[MeetingSession] = relationship("MeetingSession", back_populates="segments")


class MeetingInsight(Base, TimestampMixin):
    """A derived live insight surfaced from the rolling analysis window."""

    __tablename__ = "meeting_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("meeting_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # One of: action_item | decision | question | suggestion | risk | note.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    session: Mapped[MeetingSession] = relationship("MeetingSession", back_populates="insights")
