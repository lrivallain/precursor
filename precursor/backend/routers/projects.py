"""GitHub Projects v2 endpoints — list projects, read a board, move a card.

Columns are auto-generated from each project's Status single-select field via
the GraphQL API; moving a card issues an ``updateProjectV2ItemFieldValue``
mutation. Every endpoint is gated behind the same ``github_repo`` +
``issue_associations_enabled`` requirements as the rest of the GitHub surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import get_session
from precursor.backend.schemas.projects import (
    ItemStatusResult,
    ItemStatusUpdate,
    ProjectBoard,
    ProjectSummary,
)
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import (
    GitHubClient,
    GitHubInsufficientScopeError,
    GitHubRepoNotAccessibleError,
)

router = APIRouter(prefix="/api/github/projects", tags=["github"])


async def _resolve_repo(repo: str | None, session: AsyncSession) -> str:
    if not await resolve_issue_associations_enabled(session):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "GitHub issue associations are disabled. Enable the feature in Settings → GitHub.",
        )
    target = repo or await resolve_global_github_repo(session)
    if not target:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub repository configured. Set one in Settings or pass `repo`.",
        )
    return target


async def _require_token(session: AsyncSession) -> str:
    token = await resolve_github_token(session)
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub token available. Configure one in Settings, "
            "or sign in with `gh auth login`.",
        )
    return token


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    repo: str | None = None,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectSummary]:
    target = await _resolve_repo(repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
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
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ProjectBoard:
    await _resolve_repo(None, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
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
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ItemStatusResult:
    await _resolve_repo(None, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        updated = await client.set_project_item_status(
            project_id=project_id,
            item_id=item_id,
            field_id=payload.field_id,
            option_id=payload.option_id,
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Failed to update project item: {exc}"
        ) from exc
    finally:
        await client.aclose()
    return ItemStatusResult(item_id=updated, option_id=payload.option_id)
