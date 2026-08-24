"""Shared resolution of the GitHub repo + token a request should act on.

Both the core GitHub router and plugins (e.g. ``precursor-kanban``) need the
same two guards before touching the API: the issue surface must be enabled and
a repository configured, and a token must be resolvable. Keeping them here means
a plugin gets identical error messages and status codes without copying them.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_issue_associations_enabled,
)
from precursor.backend.services.github_auth import resolve_github_token


async def require_github_repo(repo: str | None, session: AsyncSession) -> str:
    """Return the ``owner/name`` to act on, or raise 403/400."""
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


async def require_github_token(session: AsyncSession) -> str:
    """Return a usable GitHub token, or raise 400 with setup guidance."""
    token = await resolve_github_token(session)
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No GitHub token available. Configure one in Settings, "
            "or sign in with `gh auth login`.",
        )
    return token
