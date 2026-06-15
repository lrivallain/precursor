"""Cached GitHub-issue context per topic.

Refreshed on-demand by the user or when the cached row is older than the
globally configured TTL (`issue_context_ttl_minutes` app setting).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, _utcnow


class IssueContextCache(Base):
    __tablename__ = "issue_context_cache"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    repo: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_title: Mapped[str] = mapped_column(String(512), nullable=False)
    issue_state: Mapped[str] = mapped_column(String(16), nullable=False)
    issue_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # JSON-encoded list[{name, color}].
    labels_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
