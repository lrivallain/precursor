"""Age-limited retention for Live meeting transcript segments.

Recorded meetings accumulate ``MeetingSegment`` rows (one per finalized
transcript phrase) that dominate the DB size of a Live session. When a retention
window is configured, this sweep **deletes** the transcript segments of sessions
that ended more than the window ago — bounding long-term growth while leaving the
session and its derived value intact.

Only the raw transcript is removed: the ``MeetingSession`` row and its insights,
notes and summary are preserved, so a cleaned-up session still shows its recap.
Only *ended* sessions are eligible (``ended_at`` is set and older than the
cutoff); an active or paused recording is never touched. Default retention is 7
days; 0 disables cleanup (keep forever).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import MeetingSegment, MeetingSession
from precursor.backend.services.app_settings import resolve_live_transcript_retention_days
from precursor.backend.services.retention_sweep import SessionFactory, cutoff, delete_rows
from precursor.backend.services.sweep_result import SweepResult

logger = logging.getLogger(__name__)


async def prune_expired_live_transcripts(
    session_factory: SessionFactory = SessionLocal,
    *,
    dry_run: bool = False,
) -> SweepResult:
    """Delete transcript segments of long-ended sessions; report the effect.

    A no-op when retention is disabled (0 days). Otherwise deletes every
    ``MeetingSegment`` belonging to a session whose ``ended_at`` is set and older
    than ``now - retention``. With ``dry_run`` the rows are only measured, so the
    settings UI can preview a sweep before committing to it.
    """
    async with session_factory() as session:
        retention_days = await resolve_live_transcript_retention_days(session)
        if retention_days <= 0:
            return SweepResult()
        expired_sessions = select(MeetingSession.id).where(
            MeetingSession.ended_at.is_not(None),
            MeetingSession.ended_at < cutoff(retention_days),
        )
        result = await delete_rows(
            session,
            MeetingSegment.text,
            (MeetingSegment.session_id.in_(expired_sessions),),
            dry_run=dry_run,
        )
        if dry_run:
            return result
        await session.commit()
        if result.rows:
            logger.info(
                "Deleted %d Live transcript segment(s) from session(s) ended over %d day(s) ago",
                result.rows,
                retention_days,
            )
        return result
