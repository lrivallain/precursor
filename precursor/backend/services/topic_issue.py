"""Create a GitHub issue from a new topic and return its linkage.

Used when a topic is created with ``create_linked_issue`` set: the issue title
is prefixed with the ancestor topic chain (``[Grandparent / Parent] Title``)
and the body is the topic description. The caller stores the returned repo +
issue number on the topic so the two stay associated.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Topic
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import GitHubClient


async def _ancestor_titles(session: AsyncSession, parent_id: int | None) -> list[str]:
    """Ancestor topic titles for `parent_id`, ordered root-first."""
    titles: list[str] = []
    seen: set[int] = set()
    while parent_id is not None and parent_id not in seen:
        seen.add(parent_id)
        parent = await session.get(Topic, parent_id)
        if parent is None:
            break
        titles.append(parent.title)
        parent_id = parent.parent_id
    titles.reverse()
    return titles


def build_issue_title(ancestor_titles: list[str], topic_title: str) -> str:
    """`[A / B] Title` when there are ancestors, else just `Title`."""
    if ancestor_titles:
        return f"[{' / '.join(ancestor_titles)}] {topic_title}"
    return topic_title


async def create_linked_issue(
    session: AsyncSession,
    *,
    parent_id: int | None,
    title: str,
    description: str | None,
    repo_override: str | None = None,
) -> tuple[str, int]:
    """Create a GitHub issue for a topic and return ``(repo, issue_number)``.

    Raises HTTPException on any misconfiguration (feature disabled, missing repo
    or token, GitHub error) so the calling router surfaces a clear message and
    the topic is not persisted.
    """
    if not await resolve_issue_associations_enabled(session):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "GitHub issue associations are disabled. Enable the feature in Settings → GitHub.",
        )
    repo = (repo_override or "").strip() or await resolve_global_github_repo(session)
    if not repo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub repository configured. Set one on the topic or in Settings.",
        )
    token = await resolve_github_token(session)
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub token available. Configure one in Settings, "
            "or sign in with `gh auth login`.",
        )

    ancestors = await _ancestor_titles(session, parent_id)
    issue_title = build_issue_title(ancestors, title)

    gh = GitHubClient(token=token)
    try:
        created = await gh.create_issue(repo, title=issue_title, body=description or "")
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to create issue: {exc}") from exc
    finally:
        await gh.aclose()

    return repo, int(created["number"])
