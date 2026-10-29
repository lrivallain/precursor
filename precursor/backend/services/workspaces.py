"""Workspace lifecycle shared by the REST router and the ``precursor`` MCP server.

Kept free of router imports so the stdio MCP subprocess can create a workspace
without pulling in the LLM stack.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import DEFINITIONS_WORKSPACE_SLUG, get_settings
from precursor.backend.models import Workspace
from precursor.backend.services import workspace_git as git
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.slugs import slugify

# ``precursor`` MCP tools that only read workspace trees, and those that change
# them (or create a workspace). Shared by the nested-turn tool filter and the
# agent permission carve-out.
WORKSPACE_READ_TOOLS = frozenset({"list_workspaces", "list_workspace_files", "read_workspace_file"})
WORKSPACE_WRITE_TOOLS = frozenset(
    {"create_workspace", "create_workspace_file", "write_workspace_file", "create_workspace_folder"}
)


class WorkspaceError(Exception):
    """A workspace couldn't be created; the message is safe to show the caller."""


def workspace_root(ws: Workspace) -> Path:
    """Repository working-copy root on disk."""
    return Path(get_settings().workspaces_dir) / ws.slug


def browse_root(ws: Workspace) -> Path:
    """File-browser root (repo root, or the configured subdir within it)."""
    root = workspace_root(ws)
    if ws.subdir:
        return root / ws.subdir.strip("/")
    return root


async def allocate_slug(session: AsyncSession, base: str) -> str:
    base = base or "workspace"
    candidate = base
    n = 2
    while True:
        existing = (
            await session.execute(select(Workspace.id).where(Workspace.slug == candidate))
        ).first()
        # The built-in definitions workspace's slug is kept for it, even while
        # it doesn't exist yet.
        if existing is None and candidate != DEFINITIONS_WORKSPACE_SLUG:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


async def create_workspace(
    session: AsyncSession,
    *,
    name: str,
    kind: Literal["git", "local"] = "git",
    repo_url: str | None = None,
    branch: str = "main",
    subdir: str | None = None,
    slug: str | None = None,
) -> Workspace:
    """Create a workspace row and its on-disk tree (empty folder, or a clone)."""
    is_local = kind == "local"
    if not is_local and not (repo_url or "").strip():
        raise WorkspaceError("A repository URL is required for a git workspace.")
    if not is_local and not git.git_available():
        raise WorkspaceError(
            "git is not installed on the server — install it to use git workspaces."
        )

    ws = Workspace(
        name=name,
        slug=await allocate_slug(session, slugify(slug or name)),
        kind="local" if is_local else "git",
        repo_url=None if is_local else (repo_url or "").strip(),
        branch=branch.strip() or "main",
        subdir=(subdir or "").strip() or None,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    dest = workspace_root(ws)
    if is_local:
        # A local workspace is just an empty folder we own under workspaces_dir.
        dest.mkdir(parents=True, exist_ok=True)
    else:
        # Clone inline. On failure, keep the row so the user can retry via re-clone.
        token = await resolve_github_token(session)
        try:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            await git.clone(ws.repo_url or "", dest, ws.branch, token)
        except git.GitError as exc:
            raise WorkspaceError(str(exc)) from exc

    ws.cloned_at = datetime.now(UTC)
    ws.last_synced_at = ws.cloned_at
    await session.commit()
    await session.refresh(ws)
    return ws
