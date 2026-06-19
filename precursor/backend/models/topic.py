"""Topic model — a conversation thread, optionally linked to a GitHub issue."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.message import Message
    from precursor.backend.models.reminder import Reminder
    from precursor.backend.models.topic_schedule import TopicSchedule


class Topic(Base, TimestampMixin):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Discriminates the topic's role in the tree:
    #   "standard"      — a normal conversation thread (the default).
    #   "schedule_root" — the single system folder that hosts scheduled topics.
    #   "scheduled"     — a topic driven by a recurring schedule (see TopicSchedule).
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="standard", server_default="standard"
    )
    # URL-friendly identifier. Stable across title edits unless the user
    # explicitly changes it via the settings panel.
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tree layout — self-referential parent/child relationship. We do NOT
    # cascade deletes: when a parent is removed, the API layer reparents
    # children up one level so they are not lost. The DB-level FK uses
    # SET NULL as a safety net for any out-of-band deletion.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent: Mapped[Topic | None] = relationship(
        "Topic",
        remote_side="Topic.id",
        back_populates="children",
    )
    children: Mapped[list[Topic]] = relationship(
        "Topic",
        back_populates="parent",
    )

    # GitHub issue link (owner/repo + issue number). Repo defaults to global setting
    # when null, allowing topics to point at a different repo when needed.
    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Assistant Role assigned to this topic. Null resolves to the default role
    # (no persona injected). SET NULL on delete so removing a role reverts every
    # topic that used it back to the default.
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Timestamp of the last time the user opened this topic. Used to compute
    # the sidebar unread badge (non-user messages newer than this are unread).
    # Null means "never explicitly opened" — treated as fully read so old topics
    # don't show as unread retroactively.
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # User-marked "pinned" flag. Pinned topics surface in a dedicated
    # section at the top of the sidebar, in addition to their normal place
    # in the tree.
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    # When non-null, the topic is archived: hidden from the main tree but kept
    # intact (issue link, parent_id, messages…) so it can be restored later.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="topic",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    # Recurrence config + run state for "scheduled" topics. One-to-one; null
    # for standard topics. Deleting the topic cascades to its schedule.
    schedule: Mapped[TopicSchedule | None] = relationship(
        "TopicSchedule",
        back_populates="topic",
        cascade="all, delete-orphan",
        uselist=False,
    )

    # Optional one-shot reminder. One-to-one; deleting the topic cascades to it.
    reminder: Mapped[Reminder | None] = relationship(
        "Reminder",
        back_populates="topic",
        cascade="all, delete-orphan",
        uselist=False,
    )
