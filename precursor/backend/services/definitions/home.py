"""The built-in "Definitions" workspace that holds definition files.

By default the definitions folder *is* a workspace — local, slug
``definitions`` — so agent, workflow and summary template files can be
browsed, edited and checked in the Files section like any other. It is created
on demand: at startup when Agents mode is on (or files mode is forced), and
whenever the export, the migration or a new summary template is about to write
there.

When ``PRECURSOR_DEFINITIONS_DIR`` or ``PRECURSOR_DEFINITIONS_WORKSPACE`` puts
the definitions somewhere else, there is no built-in workspace: the folder
lives where it was pointed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import DEFINITIONS_WORKSPACE_SLUG, get_settings
from precursor.backend.models import Workspace
from precursor.backend.services.definitions.loader import kind_for

logger = logging.getLogger(__name__)

WORKSPACE_NAME = "Definitions"
# What it was called while it only held agents and workflows: renamed in place.
_LEGACY_WORKSPACE_NAME = "Agents & workflows"


async def ensure_definitions_workspace(session: AsyncSession) -> Workspace | None:
    """The built-in workspace, created (with its folder) if missing.

    ``None`` when the definitions are configured to live elsewhere. Commits
    only when it creates something.
    """
    settings = get_settings()
    if not settings.definitions_in_builtin_workspace:
        return None
    ws = (
        await session.execute(select(Workspace).where(Workspace.slug == DEFINITIONS_WORKSPACE_SLUG))
    ).scalar_one_or_none()
    root = Path(settings.definitions_dir)
    if ws is None:
        now = datetime.now(UTC)
        ws = Workspace(
            name=WORKSPACE_NAME,
            slug=DEFINITIONS_WORKSPACE_SLUG,
            kind="local",
            branch="main",
            cloned_at=now,
            last_synced_at=now,
        )
        session.add(ws)
        await session.commit()
        logger.info("Created the %r workspace for definition files at %s", WORKSPACE_NAME, root)
    elif ws.kind == "local" and ws.name == _LEGACY_WORKSPACE_NAME:
        ws.name = WORKSPACE_NAME
        await session.commit()
    elif ws.kind != "local" or ws.name != WORKSPACE_NAME:
        # A workspace that already had the slug before it was reserved: it is
        # adopted as the home of the definitions, files and all.
        logger.warning(
            "Workspace %r (slug %r) now also holds the definition files",
            ws.name,
            DEFINITIONS_WORKSPACE_SLUG,
        )
    root.mkdir(parents=True, exist_ok=True)
    return ws


def holds_definitions(workspace_root: Path) -> bool:
    """Whether the definitions folder is this workspace's folder, or inside it."""
    defs = Path(get_settings().definitions_dir).resolve()
    here = workspace_root.resolve()
    return defs == here or here in defs.parents


def has_definition_files(workspace_root: Path) -> bool:
    """Whether deleting this workspace would delete definition files."""
    if not holds_definitions(workspace_root):
        return False
    defs = Path(get_settings().definitions_dir)
    if not defs.is_dir():
        return False
    return any(kind_for(p.name) is not None for p in defs.rglob("*") if p.is_file())


def workspace_folder(ws: Workspace) -> Path:
    """A workspace's working copy (what ``routers.workspaces.workspace_root`` is)."""
    return Path(get_settings().workspaces_dir) / ws.slug


async def definitions_workspace(session: AsyncSession) -> Workspace | None:
    """The workspace whose folder holds the definitions, if one does."""
    for ws in (await session.execute(select(Workspace))).scalars():
        if holds_definitions(workspace_folder(ws)):
            return ws
    return None
