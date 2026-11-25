"""Wakeup model: a conversation the model paused, to be woken up later.

A model that waits on something outside the conversation (a build, a deploy, a
long job) schedules a wakeup and ends its turn. At ``wake_at`` the background
ticker (``services/wakeup_ticker``) sends ``prompt`` back into the conversation
as a new turn, and deletes the row. While the row exists the conversation is
"paused": the UI marks it so the user knows an event is expected.

A wakeup targets exactly one conversation: a topic, a chat or an agent. Each
holds at most one, so scheduling again replaces the previous one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class Wakeup(Base, TimestampMixin):
    __tablename__ = "wakeups"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN topic_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN chat_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN agent_session_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_wakeup_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ``unique`` on each FK enforces "one wakeup per conversation".
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    agent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    # Stored UTC. Indexed: the ticker selects rows with wake_at <= now.
    wake_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # The message sent back into the conversation when it wakes up.
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # What the model is waiting for, shown to the user while it is paused.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
