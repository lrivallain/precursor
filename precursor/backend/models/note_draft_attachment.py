"""Attachment model for note drafts (images staged before publishing)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.note_draft import NoteDraft


class NoteDraftAttachment(Base, TimestampMixin):
    __tablename__ = "note_draft_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_draft_id: Mapped[int] = mapped_column(
        ForeignKey("note_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mime: Mapped[str] = mapped_column(String(127), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    note_draft: Mapped[NoteDraft] = relationship("NoteDraft", back_populates="attachments")
