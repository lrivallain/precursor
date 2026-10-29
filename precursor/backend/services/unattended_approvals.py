"""Approvals raised by unattended (scheduled) runs.

A scheduled topic has no one watching, so a call that needs approval parks the
run. Left alone it would wait forever, so each parked call gets a deadline (the
schedule's ``approval_timeout_minutes``, else the ``scheduled_approval_timeout_minutes``
setting; ``0`` waits forever). Past it the call is denied with a note the model
sees and the turn resumes headlessly, so the run ends with a report instead of
dangling. Interactive approvals never expire.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import Topic
from precursor.backend.models.tool_approval import APPROVAL_DENIED, APPROVAL_PENDING, ToolApproval
from precursor.backend.models.topic_schedule import TopicSchedule
from precursor.backend.services import approvals as approval_service
from precursor.backend.services.app_settings import resolve_scheduled_approval_timeout
from precursor.backend.services.conversation_turn import (
    ResumeConflict,
    check_resumable,
    resolve_turn_settings,
    resume_turn,
)
from precursor.backend.services.events import (
    is_streaming,
    publish_approval_changed,
    publish_approval_parked,
)
from precursor.backend.services.turn_engine import build_system_context

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def timeout_minutes(session: AsyncSession, topic_id: int) -> int:
    """Minutes a parked call waits for this topic (0 = forever)."""
    override = (
        await session.execute(
            select(TopicSchedule.approval_timeout_minutes).where(TopicSchedule.topic_id == topic_id)
        )
    ).scalar_one_or_none()
    if override is not None:
        return int(override)
    return await resolve_scheduled_approval_timeout(session)


async def announce_parked(topic_id: int, only_ids: Iterable[int] | None = None) -> None:
    """Give a just-parked unattended run its deadline and tell the windows.

    Stamps the pending calls that have none yet (``only_ids`` narrows that to the
    rows this park created), then publishes the change plus a notification event.
    """
    wanted = None if only_ids is None else set(only_ids)
    async with SessionLocal() as session:
        minutes = await timeout_minutes(session, topic_id)
        deadline = datetime.now(UTC) + timedelta(minutes=minutes) if minutes > 0 else None
        rows = (
            (
                await session.execute(
                    select(ToolApproval).where(
                        ToolApproval.topic_id == topic_id,
                        ToolApproval.status == APPROVAL_PENDING,
                        ToolApproval.executed.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        count = len(rows)
        for row in rows:
            if row.expires_at is None and (wanted is None or row.id in wanted):
                row.expires_at = deadline
        await session.commit()
        topic = await session.get(Topic, topic_id)
        title = topic.title if topic is not None else "Scheduled run"
    await publish_approval_changed(topic_id=topic_id)
    if count:
        await publish_approval_parked(
            topic_id=topic_id, title=title, count=count, expires_at=deadline
        )


async def expire_overdue(now: datetime | None = None) -> list[int]:
    """Deny every approval past its deadline; returns the topics that now have none pending.

    A topic that is mid-turn is left for the next pass: its parked rows can't be
    settled while a stream is writing the same transcript.
    """
    moment = now or datetime.now(UTC)
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(ToolApproval).where(
                        ToolApproval.status == APPROVAL_PENDING,
                        ToolApproval.expires_at.is_not(None),
                        ToolApproval.expires_at <= moment,
                        ToolApproval.topic_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        touched: set[int] = set()
        for row in rows:
            if (
                row.topic_id is None
                or row.expires_at is None
                or is_streaming("topic", row.topic_id)
            ):
                continue
            waited = max(
                1, round((_utc(row.expires_at) - _utc(row.created_at)).total_seconds() / 60)
            )
            row.status = APPROVAL_DENIED
            row.decided_at = moment
            row.feedback = (
                f"Nobody answered within {waited} minute{'s' if waited != 1 else ''} "
                "(this was an unattended run), so it was not run."
            )
            touched.add(row.topic_id)
        await session.commit()
    for topic_id in sorted(touched):
        await publish_approval_changed(topic_id=topic_id)
    return sorted(touched)


async def resume_headless(topic_id: int) -> None:
    """Resume a parked unattended turn with nobody attached to its stream."""
    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return
        try:
            decided = await check_resumable(session, "topic", topic_id)
        except ResumeConflict as exc:
            # Other calls still wait on a person (or the turn is already running).
            logger.info("Not resuming topic %s after expiry: %s", topic_id, exc)
            return
        settings = replace(
            await resolve_turn_settings(session, topic_id=topic_id), restrict_precursor=True
        )
        system_prompt = await build_system_context(session, topic)
    try:
        async for _ in resume_turn(
            "topic",
            topic_id,
            system_prompt=system_prompt,
            decided=decided,
            settings=settings,
            auth_wait_timeout=0.0,
        ):
            pass
    finally:
        # The model may have reached for another gated call; give it a deadline too.
        async with SessionLocal() as session:
            parked = await approval_service.list_approvals(session, "topic", topic_id)
        fresh = [r.id for r in parked if r.status == APPROVAL_PENDING and r.expires_at is None]
        if fresh:
            await announce_parked(topic_id, fresh)
