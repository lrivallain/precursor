"""Attachment model for note drafts (images staged before publishing)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
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
    # SHA-256 hex digest of the content; the bytes live on disk under
    # ``settings.blobs_dir`` (see services/blob_store.py), not in the DB.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    note_draft: Mapped[NoteDraft] = relationship("NoteDraft", back_populates="attachments")
