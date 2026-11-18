"""Live meeting transcript segments: ingest, list, correct and discard phrases.

The browser transcribes audio and posts each final phrase here; the rest of the
live session lifecycle lives in ``routers/live.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.clock import utcnow
from precursor.backend.db import get_session
from precursor.backend.lookups import get_or_404
from precursor.backend.models import MeetingSegment, MeetingSession
from precursor.backend.schemas import (
    MeetingSegmentCreate,
    MeetingSegmentDiscard,
    MeetingSegmentDiscardResult,
    MeetingSegmentRead,
    MeetingSegmentUpdate,
)
from precursor.backend.services.retention_sweep import rowcount

router = APIRouter(prefix="/api/live", tags=["live"])


async def _get_session_or_404(session_id: int, session: AsyncSession) -> MeetingSession:
    return await get_or_404(session, MeetingSession, session_id, "Meeting session not found")


@router.get("/{session_id}/segments", response_model=list[MeetingSegmentRead])
async def list_segments(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> list[MeetingSegment]:
    await _get_session_or_404(session_id, session)
    result = await session.execute(
        select(MeetingSegment)
        .where(MeetingSegment.session_id == session_id)
        .order_by(MeetingSegment.created_at, MeetingSegment.id)
    )
    return list(result.scalars().all())


@router.post(
    "/{session_id}/segments",
    response_model=MeetingSegmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def append_segment(
    session_id: int,
    payload: MeetingSegmentCreate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSegment:
    ms = await _get_session_or_404(session_id, session)
    # First persisted phrase marks when recording actually began.
    if ms.started_at is None:
        ms.started_at = utcnow()

    segment = MeetingSegment(
        session_id=session_id,
        text=payload.text.strip(),
        speaker_label=(payload.speaker_label or "").strip() or None,
        offset_ms=payload.offset_ms,
    )
    session.add(segment)
    await session.commit()
    await session.refresh(segment)
    return segment


@router.post("/{session_id}/segments/discard", response_model=MeetingSegmentDiscardResult)
async def discard_segments(
    session_id: int,
    payload: MeetingSegmentDiscard,
    session: AsyncSession = Depends(get_session),
) -> MeetingSegmentDiscardResult:
    """Delete several transcript phrases at once.

    Recovers a transcript polluted by a recording left running after the
    meeting. Ids from another session are ignored rather than rejected, so a
    stale selection never deletes outside this transcript.
    """
    await _get_session_or_404(session_id, session)
    result = await session.execute(
        delete(MeetingSegment).where(
            MeetingSegment.session_id == session_id,
            MeetingSegment.id.in_(set(payload.ids)),
        )
    )
    await session.commit()
    return MeetingSegmentDiscardResult(deleted=rowcount(result))


@router.patch(
    "/{session_id}/segments/{segment_id}",
    response_model=MeetingSegmentRead,
)
async def update_segment(
    session_id: int,
    segment_id: int,
    payload: MeetingSegmentUpdate,
    session: AsyncSession = Depends(get_session),
) -> MeetingSegment:
    """Correct the spoken text of a single transcript phrase.

    Lets a user fix mistaken words in place (double-click to edit in the UI).
    Only the text changes; the speaker label and offset are preserved.
    """
    await _get_session_or_404(session_id, session)
    segment = await session.get(MeetingSegment, segment_id)
    if segment is None or segment.session_id != session_id:
        raise HTTPException(status_code=404, detail="Segment not found")
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Segment text cannot be empty")
    segment.text = text
    await session.commit()
    await session.refresh(segment)
    return segment
