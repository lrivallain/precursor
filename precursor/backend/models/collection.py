"""Collection model — a named group that partitions the topic tree.

A Collection is a lens over topics: the sidebar shows exactly one at a time, so
unrelated threads stay out of the way. Membership cascades down the topic tree,
so a subtree always lives in a single collection. Each collection may also
override the GitHub repository its topics create issues into, which lets one
Precursor instance drive several repos without re-typing the target.

Deliberately *not* named "workspace" — that word already belongs to the
Git-backed Markdown folders (see ``models/workspace.py``).
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin

# Accent keys usable by the frontend. Stored as a key (not a CSS class) because
# Tailwind classes must be statically analysable — the SPA maps these to full
# class strings in `lib/sections.ts`.
COLLECTION_ACCENTS = (
    "sky",
    "emerald",
    "amber",
    "violet",
    "rose",
    "cyan",
    "slate",
)
DEFAULT_COLLECTION_ACCENT = "sky"

# Name of the protected built-in collection every topic falls back to.
DEFAULT_COLLECTION_NAME = "General"
DEFAULT_COLLECTION_SLUG = "general"


class Collection(Base, TimestampMixin):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Shown in the switcher and matched case-insensitively by `/collection`.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # `owner/repo` override for the topics in this collection. Null falls back
    # to the global `github_repo` setting. A topic's own `github_repo` still
    # wins over this.
    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Default assistant role applied to *new* topics created in this collection
    # when the caller doesn't pick one explicitly. Null means "use the built-in
    # default role". Cleared to NULL when the referenced role is deleted (the
    # FK is enforced at the app layer, not the DB — see the migration).
    default_role_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    accent: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DEFAULT_COLLECTION_ACCENT,
        server_default=DEFAULT_COLLECTION_ACCENT,
    )
    # Optional lucide icon name rendered next to the collection everywhere.
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Marks the single built-in collection: protected from deletion so every
    # topic always has somewhere to live.
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
