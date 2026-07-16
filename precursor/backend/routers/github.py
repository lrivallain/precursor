"""GitHub helper endpoints — list/search issues, create new ones, fetch labels."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import get_session
from precursor.backend.models import Topic
from precursor.backend.schemas.projects import IssueComment, IssueDetail, ProjectLabel
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


class IssueCommentPayload(BaseModel):
    repo: str | None = None  # falls back to global setting
    body: str = Field(min_length=1)


class IssueLabelsPayload(BaseModel):
    repo: str | None = None  # falls back to global setting
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


@router.get("/issues/{number}", response_model=IssueDetail)
async def get_issue_detail(
    number: int,
    repo: str | None = None,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> IssueDetail:
    """Full issue/PR view + comments for the kanban card preview.

    ``repo`` targets the item's source repo (ProjectsV2 can span repos); it
    falls back to the configured global repo. Also resolves the linked
    Precursor topic, if one points at this issue in the same repo.
    """
    target = await _resolve_repo(repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        issue = await client.get_issue(target, number)
        comments = await client.list_issue_comments(target, number)
    finally:
        await client.aclose()

    linked_id, linked_title = await _find_linked_topic(session, target, number)
    return IssueDetail(
        number=issue["number"],
        title=issue["title"],
        state=issue["state"],
        url=issue.get("url"),
        body=issue.get("body") or "",
        labels=[ProjectLabel.model_validate(label) for label in issue.get("labels", [])],
        updated_at=issue.get("updated_at"),
        comments=[IssueComment.model_validate(c) for c in comments],
        linked_topic_id=linked_id,
        linked_topic_title=linked_title,
    )


async def _find_linked_topic(
    session: AsyncSession, repo: str, number: int
) -> tuple[int | None, str | None]:
    """Return the (id, title) of a topic linked to ``repo#number``, else nulls.

    A topic's effective repo is its own ``github_repo`` or the global default,
    so a topic with no explicit repo matches when ``repo`` is the global one.
    The most recently updated match wins when several topics share an issue.
    """
    global_repo = await resolve_global_github_repo(session)
    rows = (
        await session.execute(
            select(Topic)
            .where(Topic.github_issue_number == number)
            .where(Topic.archived_at.is_(None))
            .order_by(Topic.updated_at.desc())
        )
    ).scalars()
    for topic in rows:
        effective = topic.github_repo or global_repo
        if effective == repo:
            return topic.id, topic.title
    return None, None


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


@router.post("/issues/{number}/comments", response_model=IssueComment, status_code=201)
async def add_issue_comment(
    number: int,
    payload: IssueCommentPayload,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> IssueComment:
    target = await _resolve_repo(payload.repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        comment = await client.add_issue_comment(target, number, payload.body)
    finally:
        await client.aclose()
    return IssueComment.model_validate(comment)


@router.put("/issues/{number}/labels", response_model=list[ProjectLabel])
async def set_issue_labels(
    number: int,
    payload: IssueLabelsPayload,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectLabel]:
    target = await _resolve_repo(payload.repo, session)
    token = await _require_token(session)
    client = GitHubClient(token=token)
    try:
        labels = await client.set_issue_labels(target, number, payload.labels)
    finally:
        await client.aclose()
    return [ProjectLabel.model_validate(label) for label in labels]
