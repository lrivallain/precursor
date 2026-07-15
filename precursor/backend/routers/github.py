"""GitHub helper endpoints — list/search issues, create new ones, fetch labels."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import get_session
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import GitHubClient

router = APIRouter(prefix="/api/github", tags=["github"])


class IssueCreatePayload(BaseModel):
    repo: str | None = None  # falls back to global setting
    title: str = Field(min_length=1)
    body: str | None = None
    labels: list[str] = Field(default_factory=list)


class MoveItemPayload(BaseModel):
    field_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)


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


@router.get("/issues")
async def list_issues(
    repo: str | None = None,
    q: str | None = None,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    target = await _resolve_repo(repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        return await client.list_issues(target, query=q)
    finally:
        await client.aclose()


@router.post("/issues", status_code=status.HTTP_201_CREATED)
async def create_issue(
    payload: IssueCreatePayload,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    target = await _resolve_repo(payload.repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        return await client.create_issue(
            target, title=payload.title, body=payload.body, labels=payload.labels
        )
    finally:
        await client.aclose()


@router.get("/labels")
async def list_labels(
    repo: str | None = None,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    target = await _resolve_repo(repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        return await client.list_labels(target)
    finally:
        await client.aclose()


@router.get("/projects")
async def list_projects(
    repo: str | None = None,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    target = await _resolve_repo(repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        return await client.list_projects(target)
    finally:
        await client.aclose()


@router.get("/projects/{project_id}/board")
async def get_project_board(
    project_id: str,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # Validate the repo/feature flag is set — the project node id is opaque, so
    # we only need the token, but we still enforce the feature guard.
    await _resolve_repo(None, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        return await client.get_project_board(project_id)
    finally:
        await client.aclose()


@router.patch("/projects/{project_id}/items/{item_id}", status_code=status.HTTP_200_OK)
async def move_project_item(
    project_id: str,
    item_id: str,
    payload: MoveItemPayload,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _resolve_repo(None, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        await client.move_project_item(
            project_id, item_id, payload.field_id, payload.option_id
        )
        return {"status": "ok"}
    finally:
        await client.aclose()
