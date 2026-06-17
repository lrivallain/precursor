"""Chat model — a flat conversation session without tree hierarchy or GitHub issue link."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.message import Message


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

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
