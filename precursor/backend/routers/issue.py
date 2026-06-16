"""Push topic state to its linked GitHub issue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import get_session
from precursor.backend.models import IssueContextCache, Topic
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import GitHubClient

router = APIRouter(prefix="/api/topics/{topic_id}/issue", tags=["github"])


class IssuePushResult(BaseModel):
    repo: str
    issue_number: int
    issue_title: str
    issue_state: str
    issue_url: str | None


@router.post("/push", response_model=IssuePushResult)
async def push_topic_to_issue(
    topic_id: int,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> IssuePushResult:
    """Patch the linked GitHub issue's title and body to match the topic."""
    if not await resolve_issue_associations_enabled(session):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "GitHub issue associations are disabled. Enable the feature in Settings → GitHub.",
        )
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    repo = topic.github_repo or await resolve_global_github_repo(session)
    if topic.github_issue_number is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Topic has no GitHub issue number configured.",
        )
    if not repo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Topic has issue #{topic.github_issue_number} but no repository.",
        )
    token = await resolve_github_token(session)
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub token available. Configure one in Settings, "
            "or sign in with `gh auth login`.",
        )

    gh = GitHubClient(token=token)
    try:
        updated = await gh.update_issue(
            repo,
            topic.github_issue_number,
            title=topic.title,
            body=topic.description or "",
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to update issue: {exc}") from exc
    finally:
        await gh.aclose()

    # Invalidate cached context so the next read regenerates the summary.
    cached = await session.get(IssueContextCache, topic_id)
    if cached is not None:
        await session.delete(cached)
        await session.commit()

    return IssuePushResult(
        repo=repo,
        issue_number=updated["number"],
        issue_title=updated["title"],
        issue_state=updated["state"],
        issue_url=updated.get("url"),
    )
