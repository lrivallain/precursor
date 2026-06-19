"""Role model — a named, reusable persona (system prompt) attached to a discussion.

Unlike a Skill (a one-shot prompt expanded for a single turn), a Role is a
persistent persona: once assigned to a topic, chat, or workspace it is
re-applied to every turn until the user changes it. The seeded ``default`` role
carries an empty prompt and cannot be deleted or renamed.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-friendly name shown in the selector and matched by ``/role <name>``
    # (case-insensitively). Unique.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # System prompt injected into every turn while this role is assigned. Empty
    # for the default role (injects nothing).
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Marks the single built-in "default" role: protected from deletion and
    # renaming so every discussion always has a fallback.
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
