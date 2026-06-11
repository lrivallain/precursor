"""Topic model — a conversation thread, optionally linked to a GitHub issue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.message import Message


class Topic(Base, TimestampMixin):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tree layout — self-referential parent/child relationship.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    parent: Mapped["Topic | None"] = relationship(
        "Topic",
        remote_side="Topic.id",
        back_populates="children",
    )
    children: Mapped[list["Topic"]] = relationship(
        "Topic",
        back_populates="parent",
        cascade="all, delete-orphan",
    )

    # GitHub issue link (owner/repo + issue number). Repo defaults to global setting
    # when null, allowing topics to point at a different repo when needed.
    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="topic",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
