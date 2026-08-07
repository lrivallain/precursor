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

# Copilot credits move as the user works, so cache them far more briefly than
# the identity — long enough to keep repeated menu opens off the network, short
# enough that the bar reflects recent usage.
_QUOTA_TTL_SECONDS = 60
_quota_cache: dict[str, tuple[float, CopilotQuota | None]] = {}


class GitHubIdentity(BaseModel):
    login: str
    name: str | None = None
    avatar_url: str | None = None
    html_url: str | None = None


class CopilotQuota(BaseModel):
    """The user's Copilot "AI credits" (premium interactions) allowance.

    Derived from the ``premium_interactions`` quota snapshot. ``percent_used``
    is what the persona progress bar renders; ``unlimited`` plans have no bar to
    fill (the field stays at 0) and ``reset_date`` is the next monthly rollover.
    """

    plan: str | None = None
    unlimited: bool = False
    percent_used: float = 0.0
    percent_remaining: float = 100.0
    used: int = 0
    entitlement: int = 0
    remaining: int = 0
    reset_date: str | None = None


class Me(BaseModel):
    github: GitHubIdentity | None = None
    github_token_source: str = "none"


def _normalize_quota(payload: dict[str, object]) -> CopilotQuota | None:
    """Fold a ``/copilot_internal/user`` payload into the persona's quota view.

    Only the ``premium_interactions`` snapshot is the metered "AI credits"
    allowance; ``chat`` and ``completions`` are unlimited for entitled seats and
    carry no progress. Returns ``None`` when that snapshot is absent so the
    caller can hide the bar rather than show an empty one.
    """
    snapshots = payload.get("quota_snapshots")
    premium = snapshots.get("premium_interactions") if isinstance(snapshots, dict) else None
    if not isinstance(premium, dict):
        return None
    percent_remaining = float(premium.get("percent_remaining") or 0.0)
    percent_remaining = max(0.0, min(100.0, percent_remaining))
    reset_date = payload.get("quota_reset_date")
    return CopilotQuota(
        plan=payload.get("copilot_plan") if isinstance(payload.get("copilot_plan"), str) else None,
        unlimited=bool(premium.get("unlimited")),
        percent_used=round(100.0 - percent_remaining, 1),
        percent_remaining=round(percent_remaining, 1),
        used=int(premium.get("credits_used") or 0),
        entitlement=int(premium.get("entitlement") or 0),
        remaining=int(premium.get("remaining") or 0),
        reset_date=reset_date if isinstance(reset_date, str) else None,
    )


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


async def _fetch_copilot_quota(token: str) -> CopilotQuota | None:
    entry = _quota_cache.get(token)
    if entry and (time.monotonic() - entry[0]) < _QUOTA_TTL_SECONDS:
        return entry[1]
    client = GitHubClient(token=token)
    try:
        payload = await client.get_copilot_quota()
        quota = _normalize_quota(payload) if payload else None
    except httpx.HTTPError:
        quota = None
    finally:
        await client.aclose()
    _quota_cache[token] = (time.monotonic(), quota)
    return quota


@router.get("", response_model=Me)
async def get_me(session: AsyncSession = Depends(get_session)) -> Me:
    source = await github_token_source(session)
    token = await resolve_github_token(session)
    if not token:
        return Me(github=None, github_token_source=source)
    identity = await _fetch_identity(token)
    return Me(github=identity, github_token_source=source)


@router.get("/copilot", response_model=CopilotQuota | None)
async def get_copilot_quota(
    session: AsyncSession = Depends(get_session),
) -> CopilotQuota | None:
    """Copilot AI-credit usage for the persona menu, or ``null`` when unknown.

    Returns ``null`` (never an error) when no token is configured or the account
    has no Copilot seat, so the persona simply omits the usage bar.
    """
    token = await resolve_github_token(session)
    if not token:
        return None
    return await _fetch_copilot_quota(token)
