"""Workspace model — a Git-backed folder of Markdown notes.

Unlike topics (message-backed conversations), a Workspace is a working
copy of a GitHub repository on disk. The user browses and authors Markdown
files, syncs them with `git pull` / `git push`, and gets AI help authoring
content. Deliberately kept separate from the topic domain so neither has to
grow to host the other.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # URL/folder-friendly identifier; also names the on-disk clone directory.
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # "git"   — a working copy of a remote repo (clone/pull/push enabled).
    # "local" — a plain on-disk folder with no git; only file view/edit.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="git", server_default="git"
    )
    # HTTPS clone URL, e.g. https://github.com/owner/repo.git. Null/empty for
    # local (non-git) workspaces.
    repo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    branch: Mapped[str] = mapped_column(
        String(255), nullable=False, default="main", server_default="main"
    )
    # Optional subdirectory within the repo to scope the file browser to.
    # Null/empty means the repository root.
    subdir: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set once the initial clone succeeds; null while a clone is pending or
    # has failed (the row exists so the user can retry).
    cloned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Assistant Role assigned to this workspace's chat. Null resolves to the
    # default role (no persona injected). SET NULL on delete reverts to default.
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )
