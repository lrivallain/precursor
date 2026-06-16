"""AI-generated summaries of a topic's linked GitHub issue, with DB cache."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings, get_settings
from precursor.backend.db import SessionLocal, get_session
from precursor.backend.models import IssueContextCache, Topic
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
    resolve_issue_context_ttl_minutes,
    resolve_llm_model,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import GitHubClient
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage

router = APIRouter(prefix="/api/topics/{topic_id}/summary", tags=["chat"])
logger = logging.getLogger(__name__)


class IssueLabel(BaseModel):
    name: str
    color: str


class IssueSummary(BaseModel):
    repo: str
    issue_number: int
    issue_title: str
    issue_state: str
    issue_url: str | None
    labels: list[IssueLabel] = []
    summary: str
    model: str
    fetched_at: datetime
    cached: bool = False


def _cache_to_summary(cache: IssueContextCache, *, cached: bool) -> IssueSummary:
    try:
        labels = [IssueLabel(**label) for label in json.loads(cache.labels_json)]
    except (json.JSONDecodeError, TypeError):
        labels = []
    fetched_at = cache.fetched_at
    if fetched_at.tzinfo is None:
        # SQLite drops tzinfo on round-trip; we always store UTC.
        fetched_at = fetched_at.replace(tzinfo=UTC)
    return IssueSummary(
        repo=cache.repo,
        issue_number=cache.issue_number,
        issue_title=cache.issue_title,
        issue_state=cache.issue_state,
        issue_url=cache.issue_url,
        labels=labels,
        summary=cache.summary,
        model=cache.model,
        fetched_at=fetched_at,
        cached=cached,
    )


def _is_fresh(cache: IssueContextCache, ttl_minutes: int) -> bool:
    fetched = cache.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return datetime.now(UTC) - fetched < timedelta(minutes=ttl_minutes)


@router.post("", response_model=IssueSummary)
async def summarize_issue(
    topic_id: int,
    force: bool = False,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> IssueSummary:
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
            f"Topic has issue #{topic.github_issue_number} but no repository. Set 'GitHub repo' on the topic "
            "or configure a global default in Settings.",
        )

    # Cache lookup — return a fresh-enough cached entry unless the caller asks
    # for a forced refresh or the linked issue moved (repo / number changed).
    cache = await session.get(IssueContextCache, topic_id)
    if (
        cache is not None
        and not force
        and cache.repo == repo
        and cache.issue_number == topic.github_issue_number
    ):
        ttl = await resolve_issue_context_ttl_minutes(session)
        if _is_fresh(cache, ttl):
            return _cache_to_summary(cache, cached=True)

    token = resolve_github_token(settings)
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub token available. Configure one in Settings, set GITHUB_TOKEN, "
            "or sign in with `gh auth login`.",
        )

    return await refresh_issue_context(
        topic_id=topic_id,
        repo=repo,
        issue_number=topic.github_issue_number,
        token=token,
        session=session,
    )


async def refresh_issue_context(
    *,
    topic_id: int,
    repo: str,
    issue_number: int,
    token: str,
    session: AsyncSession,
) -> IssueSummary:
    """Fetch the issue + comments from GitHub, regenerate the summary, persist.

    Shared by the `/summary` endpoint and the `/gh-sync` slash command so they
    use the exact same refresh path.
    """
    gh = GitHubClient(token=token)
    try:
        issue = await gh.get_issue(repo, issue_number)
        comments = await gh.list_issue_comments(repo, issue_number)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to fetch issue: {exc}") from exc
    finally:
        await gh.aclose()

    prompt_parts: list[str] = [
        f"Issue {repo}#{issue['number']} — {issue['title']} (state: {issue['state']})",
    ]
    if issue.get("labels"):
        prompt_parts.append("Labels: " + ", ".join(label["name"] for label in issue["labels"]))
    if issue.get("body"):
        prompt_parts.append("Body:\n" + issue["body"])
    for c in comments[-15:]:
        prompt_parts.append(f"Comment by {c['user']} @ {c['updated_at']}:\n{c['body']}")

    system = (
        "You are Precursor. Given a GitHub issue's body and recent comments, "
        "produce a concise status update for someone returning to this topic. "
        "Use 4-7 bullet points. Cover: current state, latest decisions, blockers, "
        "owners or next actions, and any open questions. Plain markdown, no preamble."
    )
    user = "\n\n".join(prompt_parts)

    provider = get_llm_provider()
    model = await resolve_llm_model(session)
    chunks: list[str] = []
    try:
        async for delta in provider.stream_chat(
            model=model,
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
        ):
            chunks.append(delta)
    except Exception as exc:
        logger.warning("Summary generation failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM call failed: {exc}") from exc

    summary_text = "".join(chunks).strip()
    now = datetime.now(UTC)
    labels_json = json.dumps(issue.get("labels", []))

    # Persist the refreshed context in a fresh session so the write commits
    # independently of the request-scoped session.
    async with SessionLocal() as write_session:
        row = await write_session.get(IssueContextCache, topic_id)
        if row is None:
            row = IssueContextCache(topic_id=topic_id)
            write_session.add(row)
        row.repo = repo
        row.issue_number = issue["number"]
        row.issue_title = issue["title"]
        row.issue_state = issue["state"]
        row.issue_url = issue.get("url")
        row.labels_json = labels_json
        row.summary = summary_text
        row.model = model
        row.fetched_at = now
        await write_session.commit()

    return IssueSummary(
        repo=repo,
        issue_number=issue["number"],
        issue_title=issue["title"],
        issue_state=issue["state"],
        issue_url=issue.get("url"),
        labels=[IssueLabel(**label) for label in issue.get("labels", [])],
        summary=summary_text,
        model=model,
        fetched_at=now,
        cached=False,
    )
