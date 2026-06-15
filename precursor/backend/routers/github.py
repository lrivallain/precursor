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


def _require_token(settings: Settings) -> str:
    token = resolve_github_token(settings)
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub token available. Configure one in Settings, set GITHUB_TOKEN, "
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
    token = _require_token(settings)
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
    token = _require_token(settings)
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
    token = _require_token(settings)
    client = GitHubClient(token=token)
    try:
        return await client.list_labels(target)
    finally:
        await client.aclose()
