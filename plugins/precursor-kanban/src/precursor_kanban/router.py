"""GitHub Projects v2 endpoints — list projects, read a board, move a card.

Columns are auto-generated from each project's Status single-select field via
the GraphQL API; moving a card issues an ``updateProjectV2ItemFieldValue``
mutation. Every endpoint is gated behind the same ``github_repo`` +
``issue_associations_enabled`` requirements as the rest of the GitHub surface,
using the host's shared guards.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.plugin_api import (
    GitHubInsufficientScopeError,
    GitHubRepoNotAccessibleError,
    get_session,
    require_github_repo,
    require_github_token,
)
from precursor_kanban.client import ProjectsClient
from precursor_kanban.schemas import (
    ItemStatusResult,
    ItemStatusUpdate,
    ProjectBoard,
    ProjectSummary,
)
from precursor_kanban.sources import ProjectSource, board_config, project_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github/projects", tags=["kanban"])

#: Settings namespace, which is the plugin id. Imported lazily-ish here rather
#: than from ``plugin`` to avoid a circular import at module load.
SECTION_ID = "kanban"


async def _collect_source(client: ProjectsClient, source: ProjectSource) -> list[dict[str, Any]]:
    """Boards for one configured source, or an empty list if unreachable.

    A source that has been renamed, made private or revoked must not take the
    whole picker down with it — the other boards are still perfectly usable, and
    the settings page is where a broken entry gets fixed.

    Each board is tagged with the entry that produced it, so the picker can
    offer to remove that entry and can warn when doing so drops several boards
    at once.
    """
    kind = "pinned" if source.number is not None else "account"
    try:
        if source.number is not None:
            project = await client.get_owner_project(source.owner, source.number)
            found = [project] if project else []
        else:
            found = await client.list_owner_projects(source.owner)
    except GitHubInsufficientScopeError:
        raise
    except Exception:
        logger.warning("Skipping unreachable project source %s", source.label, exc_info=True)
        return []
    for project in found:
        project["source"] = kind
        project["source_ref"] = source.raw or source.label
    return found


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    repo: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ProjectSummary]:
    """Boards from the configured repo's owner, plus any extra sources.

    The configured owner is the default; extras are additive and de-duplicated
    by node id, so pinning a project from an account already listed changes
    nothing rather than showing it twice. Boards the user has hidden are dropped
    last, so hiding one works regardless of which source produced it.
    """
    target = await require_github_repo(repo, session)
    token = await require_github_token(session)
    client = ProjectsClient(token=token)
    try:
        # Inside the guard: this reads user-supplied settings, and nothing about
        # a bad entry there should be able to take the board's own listing down.
        config = await board_config(SECTION_ID)
        projects = await client.list_repo_projects(target)
        for project in projects:
            project["source"] = "repo"
            project["source_ref"] = None
        for source in config.sources:
            projects.extend(await _collect_source(client, source))
    except GitHubInsufficientScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except GitHubRepoNotAccessibleError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    finally:
        await client.aclose()

    # First occurrence wins, so a board owned by the configured account keeps its
    # "repo" provenance even when a redundant source also names it. That source
    # is then only removable from the settings page, which is correct: it adds
    # nothing to the picker, so there is no board to right-click for it.
    unique: dict[str, dict[str, Any]] = {}
    for project in projects:
        unique.setdefault(project["id"], project)
    visible = [
        p
        for p in unique.values()
        if p.get("owner") is None or project_key(p["owner"], p["number"]) not in config.hidden
    ]
    return [ProjectSummary.model_validate(p) for p in visible]


@router.get("/{project_id}/board", response_model=ProjectBoard)
async def get_board(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> ProjectBoard:
    await require_github_repo(None, session)
    token = await require_github_token(session)
    client = ProjectsClient(token=token)
    try:
        board = await client.get_project_board(project_id)
    except GitHubInsufficientScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    finally:
        await client.aclose()
    return ProjectBoard.model_validate(board)


@router.post("/{project_id}/items/{item_id}/status", response_model=ItemStatusResult)
async def update_item_status(
    project_id: str,
    item_id: str,
    payload: ItemStatusUpdate,
    session: AsyncSession = Depends(get_session),
) -> ItemStatusResult:
    await require_github_repo(None, session)
    token = await require_github_token(session)
    client = ProjectsClient(token=token)
    try:
        updated = await client.set_project_item_status(
            project_id=project_id,
            item_id=item_id,
            field_id=payload.field_id,
            option_id=payload.option_id,
        )
    except GitHubInsufficientScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Failed to update project item: {exc}"
        ) from exc
    finally:
        await client.aclose()
    return ItemStatusResult(item_id=updated, option_id=payload.option_id)
