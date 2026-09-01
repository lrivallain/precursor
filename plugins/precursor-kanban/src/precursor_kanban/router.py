"""GitHub Projects v2 endpoints — list projects, read a board, move a card.

Columns are auto-generated from each project's Status single-select field via
the GraphQL API; moving a card issues an ``updateProjectV2ItemFieldValue``
mutation.

A configured repository is **not** required. It is only ever read for its owner
— ``list_repo_projects`` discards the name — so it is one way of saying "list
this account's boards", equivalent to adding that account as a source. Requiring
it would block a perfectly workable setup: a token plus an explicitly configured
project. What every endpoint does need is the GitHub feature switch and a token
with the ``project`` scope; project and item ids are global GitHub node ids, so
nothing else here is repo-scoped.
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
    require_github_token,
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor_kanban.client import ProjectsClient
from precursor_kanban.schemas import (
    ItemStatusResult,
    ItemStatusUpdate,
    ProjectBoard,
    ProjectListing,
    ProjectSummary,
    UnresolvedSource,
)
from precursor_kanban.sources import ProjectSource, board_config, project_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github/projects", tags=["kanban"])

#: Settings namespace, which is the plugin id. Imported lazily-ish here rather
#: than from ``plugin`` to avoid a circular import at module load.
SECTION_ID = "kanban"


async def require_github_enabled(session: AsyncSession) -> None:
    """Gate on the GitHub feature switch alone.

    The host's ``require_github_repo`` bundles this check with "a repository is
    configured". The board wants only the first half — see the module docstring
    — so it asks for it directly rather than demanding a repo it won't use.
    """
    if not await resolve_issue_associations_enabled(session):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "GitHub issue associations are disabled. Enable the feature in Settings → GitHub.",
        )


async def _collect_source(client: ProjectsClient, source: ProjectSource) -> list[dict[str, Any]]:
    """Boards for one configured source, or an empty list if unreachable.

    A source that has been renamed, made private or revoked must not take the
    whole picker down with it — the other boards are still perfectly usable, and
    the caller reports the entry separately so it stays removable.

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


@router.get("", response_model=ProjectListing)
async def list_projects(
    repo: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> ProjectListing:
    """Boards from every configured source, plus the repo owner's when there is one.

    The configured repository is an optional default, not a precondition: with
    none set, the sources stand on their own. Boards are de-duplicated by node
    id, so naming an account the repo already covers changes nothing rather than
    listing it twice.

    Hidden boards are returned with ``hidden=True`` rather than dropped — the
    picker is the only place to unhide one. Sources that produced no boards are
    reported separately so they stay visible and removable.
    """
    await require_github_enabled(session)
    token = await require_github_token(session)
    target = repo or await resolve_global_github_repo(session)
    client = ProjectsClient(token=token)
    unresolved: list[UnresolvedSource] = []
    try:
        # Inside the guard: this reads user-supplied settings, and nothing about
        # a bad entry there should be able to take the board's own listing down.
        config = await board_config(SECTION_ID)
        projects: list[dict[str, Any]] = []
        if target:
            projects = await client.list_repo_projects(target)
            for project in projects:
                project["source"] = "repo"
                project["source_ref"] = None
        for source in config.sources:
            found = await _collect_source(client, source)
            if found:
                projects.extend(found)
            else:
                unresolved.append(
                    UnresolvedSource(
                        ref=source.raw or source.label,
                        kind="pinned" if source.number is not None else "account",
                    )
                )
    except GitHubInsufficientScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except GitHubRepoNotAccessibleError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    finally:
        await client.aclose()

    # First occurrence wins, so a board owned by the configured account keeps its
    # "repo" provenance even when a redundant source also names it.
    unique: dict[str, dict[str, Any]] = {}
    for project in projects:
        unique.setdefault(project["id"], project)
    for project in unique.values():
        owner = project.get("owner")
        project["hidden"] = bool(
            owner is not None and project_key(owner, project["number"]) in config.hidden
        )
    return ProjectListing(
        projects=[ProjectSummary.model_validate(p) for p in unique.values()],
        unresolved=unresolved,
    )


@router.get("/{project_id}/board", response_model=ProjectBoard)
async def get_board(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> ProjectBoard:
    await require_github_enabled(session)
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
    await require_github_enabled(session)
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
