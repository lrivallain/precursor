"""Memory model — long-term notes (context, preferences, facts) injected into chats."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class Memory(Base, TimestampMixin):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Free-form tag shown in the UI and prepended to the line sent to the LLM
    # (e.g. "context", "preference", "fact"). Lowercase, short.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="context")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
