"""Skill model.

A skill is a named, reusable prompt invoked via ``/name`` in chat. Content
(name, description, instructions) now lives in
``<copilot_home>/skills/<name>/SKILL.md`` so it is shared with the GitHub
Copilot CLI and other tools. This table is reduced to an **enablement record**
for those file-backed skills, plus a transitional home for *legacy* skills that
predate the file model and still carry their content here until migrated.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class Skill(Base, TimestampMixin):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Slash-command name (e.g. "to-en"), matching the on-disk folder name.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Whether a file-backed skill is enabled (offered as a slash command).
    # Ignored for legacy skills, which stay active until migrated.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # False = legacy DB-only skill whose content lives in the columns below and
    #         which exposes a "Migrate" action.
    # True  = file-backed skill; this row only tracks enablement and the content
    #         columns are cleared.
    migrated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Legacy content — only meaningful while ``migrated`` is False.
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
