"""Chat model — a flat conversation session without tree hierarchy or GitHub issue link."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.message import Message
    from precursor.backend.models.reminder import Reminder


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Assistant Role assigned to this chat. Null resolves to the default role
    # (no persona injected). SET NULL on delete reverts to default.
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Timestamp of the last time the user opened this chat. Used to compute
    # unread badge (non-user messages newer than this are unread).
    # Null means "never explicitly opened" — treated as fully read.
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # User-marked "pinned" flag. Pinned chats surface in a dedicated
    # section at the top of the chat list.
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    # When non-null, the chat is archived: hidden from the main list but kept
    # intact (messages, metadata) so it can be restored later.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    # Optional one-shot reminder. One-to-one; deleting the chat cascades to it.
    reminder: Mapped[Reminder | None] = relationship(
        "Reminder",
        back_populates="chat",
        cascade="all, delete-orphan",
        uselist=False,
    )
