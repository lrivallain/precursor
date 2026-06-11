"""GitHub helper endpoints — list/search issues, create new ones, fetch labels."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from precursor.backend.config import Settings, get_settings
from precursor.backend.services.github_client import GitHubClient

router = APIRouter(prefix="/api/github", tags=["github"])


class IssueCreatePayload(BaseModel):
    repo: str | None = None  # falls back to global setting
    title: str = Field(min_length=1)
    body: str | None = None
    labels: list[str] = Field(default_factory=list)


def _resolve_repo(repo: str | None, settings: Settings) -> str:
    target = repo or settings.github_repo
    if not target:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub repository configured. Set one in Settings or pass `repo`.",
        )
    return target


def _require_token(settings: Settings) -> str:
    if not settings.github_token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "GITHUB_TOKEN is not configured.",
        )
    return settings.github_token


@router.get("/issues")
async def list_issues(
    repo: str | None = None,
    q: str | None = None,
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    target = _resolve_repo(repo, settings)
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
) -> dict[str, Any]:
    target = _resolve_repo(payload.repo, settings)
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
) -> list[dict[str, Any]]:
    target = _resolve_repo(repo, settings)
    token = _require_token(settings)
    client = GitHubClient(token=token)
    try:
        return await client.list_labels(target)
    finally:
        await client.aclose()
