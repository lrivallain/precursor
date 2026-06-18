"""Reminder model — bring a topic or chat back up at a specific date/time.

A reminder is attached to exactly one container (a topic *or* a chat, mirroring
``Message``). At ``remind_at`` the background ticker (``services/reminder_ticker``)
posts a system message to the discussion (so it goes unread + notifies) and flips
``status`` to ``"fired"``, which surfaces the container in the sidebar's
"Reminders" section until the user acknowledges it (``/done``). At most one
reminder exists per container, so setting a new one replaces the previous.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.chat import Chat
    from precursor.backend.models.topic import Topic


class Reminder(Base, TimestampMixin):
    __tablename__ = "reminders"
    __table_args__ = (
        # Exactly one container — same invariant as Message (app-layer enforced too).
        CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) "
            "OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_reminder_container",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ``unique`` on each FK enforces "one reminder per topic / per chat".
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
        index=True,
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
        index=True,
    )

    # When to resurface the discussion (stored UTC). Indexed: the ticker selects
    # rows with remind_at <= now.
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # Optional free-text shown in the posted message and the sidebar entry.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "scheduled" — waiting for remind_at. "fired" — due, awaiting acknowledgment.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="scheduled", server_default="scheduled"
    )
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    topic: Mapped[Topic | None] = relationship("Topic", back_populates="reminder")
    chat: Mapped[Chat | None] = relationship("Chat", back_populates="reminder")
