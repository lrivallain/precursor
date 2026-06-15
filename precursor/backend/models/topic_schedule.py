"""TopicSchedule — recurrence config and run state for a scheduled topic.

A scheduled topic (``Topic.kind == "scheduled"``) has exactly one schedule row
holding *when* it runs (interval-based recurrence) and the last-known run state.
The background scheduler (``services/scheduler.py``) polls due rows, claims them
via the ``status``/``lease_until`` lease, runs the turn, then advances
``next_run_at``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.topic import Topic


class TopicSchedule(Base, TimestampMixin):
    __tablename__ = "topic_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # When false, the ticker ignores this schedule (paused, not deleted).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    # When true, the topic's prior messages are wiped before each run so every
    # run starts from a clean slate (no accumulated history).
    clear_context: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # The prompt sent as the user turn on each run.
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Interval-based recurrence: run every N seconds. The UI exposes this as a
    # value + unit (minutes / hours / days).
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # Daily-at-time recurrence: when set, the schedule runs once per allowed
    # day at this minute-of-day (0..1439) in ``timezone`` instead of using
    # ``interval_seconds``. Null => interval mode.
    run_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # IANA timezone name used to interpret ``run_at_minute`` (e.g.
    # "Europe/Paris"). Defaults to UTC; unknown names fall back to UTC.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    # Allowed days of week as a 7-bit mask: bit 0 = Monday … bit 6 = Sunday
    # (matching ``datetime.weekday()``). 127 = every day. A computed run time
    # that lands on a disallowed day is pushed forward to the next allowed day.
    days_of_week: Mapped[int] = mapped_column(
        Integer, nullable=False, default=127, server_default="127"
    )

    # Next due time. The ticker selects rows with next_run_at <= now.
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Run lifecycle: "idle" | "running" | "ok" | "error".
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="idle", server_default="idle"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # While running, the worker holds a lease. A row stuck in "running" past
    # its lease_until is considered orphaned (process crash) and reclaimable.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    topic: Mapped[Topic] = relationship("Topic", back_populates="schedule")
