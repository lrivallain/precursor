"""Skill model — a named, reusable prompt that the user invokes via slash command."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class Skill(Base, TimestampMixin):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Slash-command name (e.g. "to-en"). Must be unique, lowercase, hyphen-friendly.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Optional short description shown in the command picker.
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Full instructions injected when the skill is invoked.
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
