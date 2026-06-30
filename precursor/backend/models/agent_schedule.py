"""AgentSchedule — recurrence config and run state for a scheduled agent session.

An agent session (``AgentSession``) may have at most one schedule row holding
*when* it re-runs (interval- or daily-at-time recurrence) and the last-known run
state. The background scheduler (``services/scheduler.py``) polls due rows, claims
them via the ``status``/``lease_until`` lease, triggers the agent's task again,
then advances ``next_run_at``.

Unlike ``TopicSchedule`` there is no ``prompt`` column: a scheduled run replays
the agent's own ``task_prompt`` (optionally from a fresh context when
``clear_context`` is set), so the instructions live in one place on the agent.
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
    from precursor.backend.models.agent_session import AgentSession


class AgentSchedule(Base, TimestampMixin):
    __tablename__ = "agent_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # When false, the ticker ignores this schedule (paused, not deleted).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    # When true, each run wipes the agent's prior context (keeping its public id)
    # and replays ``task_prompt`` from a clean slate; otherwise the task is sent
    # as a follow-up into the existing conversation. Defaults to true because a
    # recurring agent task rarely wants an ever-growing transcript.
    clear_context: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Interval-based recurrence: run every N seconds. The UI exposes this as a
    # value + unit (minutes / hours / days).
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # Daily-at-time recurrence: when set, the schedule runs once per allowed day
    # at this minute-of-day (0..1439) in ``timezone`` instead of using
    # ``interval_seconds``. Null => interval mode.
    run_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # IANA timezone name used to interpret ``run_at_minute`` (e.g.
    # "Europe/Paris"). Defaults to UTC; unknown names fall back to UTC.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    # Allowed days of week as a 7-bit mask: bit 0 = Monday … bit 6 = Sunday
    # (matching ``datetime.weekday()``). 127 = every day.
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
    # While triggering, the worker holds a lease. A row stuck in "running" past
    # its lease_until is considered orphaned (process crash) and reclaimable.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_session: Mapped[AgentSession] = relationship("AgentSession", back_populates="schedule")
