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

    # JSON-encoded dict[str, str] mapping a raw diarization label (e.g.
    # "Guest-2") to a user-chosen display name (e.g. "Thomas"). Segments keep
    # their raw label; names are applied at display + analysis time so renaming
    # one speaker updates every past and future phrase from that voice.
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
    # from renamed speakers + any linked meeting's invitees; user-editable.
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
