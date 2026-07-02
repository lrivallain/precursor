"""AgentEventRecord — durable archive of an agent session's workflow timeline.

The Copilot SDK's ``session.get_events`` is **per-connection**: a resumed session
only replays ``SessionStartData``, not the full turn/tool/reasoning history. So
the workflow timeline — which the manager otherwise keeps only in memory — would
be empty after a process restart. We mirror every streamed, normalised event
here so the timeline survives restarts (and live-session teardown, e.g. when a
topic is linked). Append-only; rows are removed when the agent is deleted.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base


class AgentEventRecord(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    agent_session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The normalised ``schemas.agent.AgentEvent`` serialised as JSON. Stored as an
    # opaque blob: the UI shape can evolve without a migration, and the manager
    # owns (de)serialisation.
    payload: Mapped[str] = mapped_column(Text, nullable=False)

    # Python-side default (tz-aware, microsecond) mirrors ``TimestampMixin`` so
    # this compares reliably against the tz-aware ``AgentSession.last_read_at``
    # when computing the unread badge. ``server_default`` stays as a fallback for
    # any raw insert.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
