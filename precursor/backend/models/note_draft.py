"""Draft note scratchpad persisted per topic/chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.chat import Chat
    from precursor.backend.models.note_draft_attachment import NoteDraftAttachment
    from precursor.backend.models.topic import Topic


class NoteDraft(Base, TimestampMixin):
    __tablename__ = "note_drafts"
    __table_args__ = (
        CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) "
            "OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_note_draft_container",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    topic: Mapped[Topic | None] = relationship("Topic", back_populates="note_draft")
    chat: Mapped[Chat | None] = relationship("Chat", back_populates="note_draft")
    attachments: Mapped[list[NoteDraftAttachment]] = relationship(
        "NoteDraftAttachment",
        back_populates="note_draft",
        cascade="all, delete-orphan",
        order_by="NoteDraftAttachment.id",
    )
