"""Live meeting assistant endpoints — session CRUD.

A meeting session records an ongoing meeting: the browser transcribes audio
into segments while the backend derives live insights. This router owns the
session lifecycle (create / list / get / update / delete); transcript ingestion,
live analysis, and summary attachment land in later phases.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import MeetingSession, Topic
from precursor.backend.schemas import (
    MeetingSessionCreate,
    MeetingSessionRead,
    MeetingSessionUpdate,
)
from precursor.backend.services.events import publish_meeting_changed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _SLUG_RE.sub("-", ascii_only.lower()).strip("-")[:80]


async def _allocate_slug(session: AsyncSession, base: str) -> str:
    base = base or "session"
    candidate = base
    n = 2
    while True:
        existing = (
            await session.execute(select(MeetingSession.id).where(MeetingSession.slug == candidate))
        ).first()
        if existing is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


async def _get_session_or_404(session_id: int, session: AsyncSession) -> MeetingSession:
    ms = await session.get(MeetingSession, session_id)
    if ms is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting session not found")
    return ms


async def _validate_topic(topic_id: int | None, session: AsyncSession) -> None:
    if topic_id is None:
        return
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Attached topic not found")


@router.get("", response_model=list[MeetingSessionRead])
async def list_sessions(
    session: AsyncSession = Depends(get_session),
) -> list[MeetingSession]:
    result = await session.execute(
        select(MeetingSession).order_by(MeetingSession.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=MeetingSessionRead, status_code=status.HTTP_201_CREATED)
async def create_session_endpoint(
    payload: MeetingSessionCreate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    await _validate_topic(payload.topic_id, session)

    title = (payload.title or "").strip() or (f"Live session — {datetime.now(UTC):%Y-%m-%d %H:%M}")
    slug = await _allocate_slug(session, _slugify(payload.slug or title))

    ms = MeetingSession(
        title=title,
        slug=slug,
        status="active",
        language=(payload.language or "").strip() or None,
        topic_id=payload.topic_id,
    )
    session.add(ms)
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


@router.get("/{session_id}", response_model=MeetingSessionRead)
async def get_session_endpoint(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> MeetingSession:
    return await _get_session_or_404(session_id, session)


@router.patch("/{session_id}", response_model=MeetingSessionRead)
async def update_session_endpoint(
    session_id: int,
    payload: MeetingSessionUpdate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSession:
    ms = await _get_session_or_404(session_id, session)
    data = payload.model_dump(exclude_unset=True)

    if "topic_id" in data:
        await _validate_topic(data["topic_id"], session)
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip() or ms.title
    if "language" in data:
        data["language"] = (data["language"] or "").strip() or None
    # Transitioning to "ended" stamps ended_at once.
    if data.get("status") == "ended" and ms.ended_at is None:
        ms.ended_at = datetime.now(UTC)

    for key, value in data.items():
        setattr(ms, key, value)
    await session.commit()
    await session.refresh(ms)
    await publish_meeting_changed(ms.id)
    return ms


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session_endpoint(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    ms = await _get_session_or_404(session_id, session)
    await session.delete(ms)
    await session.commit()
    await publish_meeting_changed(session_id)
