"""Attachment model — a binary blob linked to a user message."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.chat import Chat
    from precursor.backend.models.message import Message
    from precursor.backend.models.topic import Topic


class Attachment(Base, TimestampMixin):
    __tablename__ = "attachments"
    __table_args__ = (
        # Mirror Message: an attachment belongs to exactly one container
        # (a topic or a chat), enforced app-side too.
        CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_attachment_container",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # Null while the attachment has been uploaded but the user hasn't yet sent
    # the message it belongs to (so the composer can show a preview chip and
    # let the user cancel before commit).
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    mime: Mapped[str] = mapped_column(String(127), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    topic: Mapped[Topic | None] = relationship("Topic")
    chat: Mapped[Chat | None] = relationship("Chat")
    message: Mapped[Message | None] = relationship("Message", back_populates="attachments")
