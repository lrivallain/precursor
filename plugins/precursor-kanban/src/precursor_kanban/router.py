"""GitHub Projects v2 endpoints — list projects, read a board, move a card.

Columns are auto-generated from each project's Status single-select field via
the GraphQL API; moving a card issues an ``updateProjectV2ItemFieldValue``
mutation. Every endpoint is gated behind the same ``github_repo`` +
``issue_associations_enabled`` requirements as the rest of the GitHub surface,
using the host's shared guards.
"""

from __future__ import annotations

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

router = APIRouter(prefix="/api/github/projects", tags=["kanban"])


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    repo: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ProjectSummary]:
    target = await require_github_repo(repo, session)
    token = await require_github_token(session)
    client = ProjectsClient(token=token)
    try:
        projects = await client.list_repo_projects(target)
    except GitHubInsufficientScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except GitHubRepoNotAccessibleError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    finally:
        await client.aclose()
    return [ProjectSummary.model_validate(p) for p in projects]


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
