"""Current-user endpoint — surfaces the connected identity for the sidebar persona."""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.services.github_auth import (
    github_token_source,
    resolve_github_token,
)
from precursor.backend.services.github_client import GitHubClient

router = APIRouter(prefix="/api/me", tags=["me"])

_CACHE_TTL_SECONDS = 300
_identity_cache: dict[str, tuple[float, GitHubIdentity | None]] = {}


class GitHubIdentity(BaseModel):
    login: str
    name: str | None = None
    avatar_url: str | None = None
    html_url: str | None = None


class Me(BaseModel):
    github: GitHubIdentity | None = None
    github_token_source: str = "none"


async def _fetch_identity(token: str) -> GitHubIdentity | None:
    entry = _identity_cache.get(token)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL_SECONDS:
        return entry[1]
    client = GitHubClient(token=token)
    try:
        data = await client.get_authenticated_user()
        identity: GitHubIdentity | None = GitHubIdentity(**data)
    except (httpx.HTTPError, KeyError):
        identity = None
    finally:
        await client.aclose()
    _identity_cache[token] = (time.monotonic(), identity)
    return identity


@router.get("", response_model=Me)
async def get_me(session: AsyncSession = Depends(get_session)) -> Me:
    source = await github_token_source(session)
    token = await resolve_github_token(session)
    if not token:
        return Me(github=None, github_token_source=source)
    identity = await _fetch_identity(token)
    return Me(github=identity, github_token_source=source)
