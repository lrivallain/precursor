"""TopicSummary — the editable status brief shown above a topic's transcript.

One row per topic, created lazily: a topic has no summary until the user asks
for one (``/update-summary`` or the header button). The row holds the *live*
markdown (``content``) plus, when a regeneration lands on top of text the user
edited by hand, the model's proposal (``pending_content``) kept aside so the
user can accept or refuse it change by change — see
``services/topic_summary.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.topic import Topic


class TopicSummary(Base, TimestampMixin):
    __tablename__ = "topic_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # The markdown currently shown in the panel.
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Whether the panel is expanded. `/hide-summary` flips this off without
    # discarding the text.
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    # Set once the user saves a manual edit. Regeneration then goes through the
    # review flow instead of overwriting, and the prompt is told to preserve
    # the existing wording wherever it is still accurate.
    user_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # A model proposal awaiting the user's per-change review. Null when there
    # is nothing to review.
    pending_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pending_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    topic: Mapped[Topic] = relationship("Topic", back_populates="summary")
