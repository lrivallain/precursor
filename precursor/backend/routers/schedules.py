"""Scheduled-topics router — CRUD over recurring topics and their schedules.

A scheduled topic is a normal Topic (``kind == "scheduled"``) parented under the
single system folder (``kind == "schedule_root"``, slug ``scheduled``), plus a
TopicSchedule row holding its recurrence config and run state. The background
scheduler (services/scheduler.py) executes due schedules.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Message, MessageRole, Topic, TopicSchedule
from precursor.backend.schemas.schedule import (
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from precursor.backend.services.events import (
    publish_message_changed,
    publish_topic_changed,
    set_current_client_id,
)
from precursor.backend.services.schedule_timing import compute_next_run
from precursor.backend.services.scheduler import get_scheduler
from precursor.backend.services.slugs import allocate_unique_slug, slugify

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

SCHEDULE_ROOT_SLUG = "scheduled"
SCHEDULE_ROOT_TITLE = "Scheduled"


def _now() -> datetime:
    return datetime.now(UTC)


async def ensure_schedule_root(session: AsyncSession) -> Topic:
    """Return the single schedule-root folder, creating it on first use."""
    result = await session.execute(select(Topic).where(Topic.kind == "schedule_root"))
    root = result.scalar_one_or_none()
    if root is not None:
        return root
    root = Topic(
        title=SCHEDULE_ROOT_TITLE,
        slug=await allocate_unique_slug(session, SCHEDULE_ROOT_SLUG),
        kind="schedule_root",
    )
    session.add(root)
    await session.commit()
    await session.refresh(root)
    return root


@router.get("", response_model=list[ScheduleRead])
async def list_schedules(
    session: AsyncSession = Depends(get_session),
) -> list[TopicSchedule]:
    result = await session.execute(select(TopicSchedule).order_by(TopicSchedule.next_run_at))
    return list(result.scalars().all())


@router.get("/{topic_id}", response_model=ScheduleRead)
async def get_schedule(
    topic_id: int, session: AsyncSession = Depends(get_session)
) -> TopicSchedule:
    schedule = await _get_for_topic(session, topic_id)
    return schedule


@router.post("", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate,
    session: AsyncSession = Depends(get_session),
) -> TopicSchedule:
    root = await ensure_schedule_root(session)

    base = slugify(payload.title) or "scheduled-topic"
    topic = Topic(
        title=payload.title,
        slug=await allocate_unique_slug(session, base),
        kind="scheduled",
        parent_id=root.id,
    )
    session.add(topic)
    await session.flush()  # assign topic.id

    schedule = TopicSchedule(
        topic_id=topic.id,
        enabled=payload.enabled,
        prompt=payload.prompt,
        interval_seconds=payload.interval_seconds,
        days_of_week=payload.days_of_week,
        run_at_minute=payload.run_at_minute,
        timezone=payload.timezone,
        clear_context=payload.clear_context,
        # First run is the next occurrence (interval or daily-at-time),
        # skipping disallowed weekdays; "Run now" can pull it earlier.
        next_run_at=compute_next_run(
            _now(),
            payload.interval_seconds,
            payload.days_of_week,
            payload.run_at_minute,
            payload.timezone,
        )
        if payload.enabled
        else None,
        status="idle",
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    await publish_topic_changed(topic.id)
    return schedule


@router.patch("/{topic_id}", response_model=ScheduleRead)
async def update_schedule(
    topic_id: int,
    payload: ScheduleUpdate,
    session: AsyncSession = Depends(get_session),
) -> TopicSchedule:
    schedule = await _get_for_topic(session, topic_id)
    data = payload.model_dump(exclude_unset=True)

    if data.get("title"):
        topic = await session.get(Topic, topic_id)
        if topic is not None:
            topic.title = data["title"]

    if data.get("prompt"):
        schedule.prompt = data["prompt"]

    if data.get("interval_seconds"):
        schedule.interval_seconds = data["interval_seconds"]

    if data.get("days_of_week"):
        schedule.days_of_week = data["days_of_week"]

    if data.get("timezone"):
        schedule.timezone = data["timezone"]

    if "clear_context" in data and data["clear_context"] is not None:
        schedule.clear_context = data["clear_context"]

    # run_at_minute is tri-state: omitted = unchanged, int = daily-at-time,
    # explicit null = back to interval mode.
    cadence_changed = (
        "interval_seconds" in data
        or "days_of_week" in data
        or "timezone" in data
        or "run_at_minute" in data
    )
    if "run_at_minute" in data:
        schedule.run_at_minute = data["run_at_minute"]

    # Re-anchor the next run from now whenever cadence/days/time changed, so
    # edits take effect promptly and respect the (possibly new) restriction.
    if cadence_changed and schedule.enabled:
        schedule.next_run_at = compute_next_run(
            _now(),
            schedule.interval_seconds,
            schedule.days_of_week,
            schedule.run_at_minute,
            schedule.timezone,
        )

    if "enabled" in data and data["enabled"] is not None:
        schedule.enabled = data["enabled"]
        if schedule.enabled and schedule.next_run_at is None:
            schedule.next_run_at = compute_next_run(
                _now(),
                schedule.interval_seconds,
                schedule.days_of_week,
                schedule.run_at_minute,
                schedule.timezone,
            )
        if not schedule.enabled:
            schedule.next_run_at = None

    await session.commit()
    await session.refresh(schedule)
    await publish_topic_changed(topic_id)
    return schedule


@router.post("/{topic_id}/run", response_model=ScheduleRead)
async def run_now(topic_id: int, session: AsyncSession = Depends(get_session)) -> TopicSchedule:
    """Pull the next run forward so the ticker picks it up immediately."""
    schedule = await _get_for_topic(session, topic_id)
    if schedule.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "Run already in progress")
    # Force-clear any stale run state (e.g. a schedule left at "error" or with a
    # lingering lease) so nothing blocks the ticker from claiming it.
    await session.execute(
        update(TopicSchedule)
        .where(TopicSchedule.topic_id == topic_id)
        .values(
            enabled=True,
            next_run_at=_now(),
            status="idle",
            lease_until=None,
            last_error=None,
        )
    )
    # Broadcast the confirmation to *all* windows including this one: the run_now
    # request carries this client's id, which would otherwise echo-suppress the
    # event in the originating window (forcing a manual refresh).
    set_current_client_id(None)
    session.add(
        Message(
            topic_id=topic_id,
            role=MessageRole.SYSTEM,
            content="Run now accepted — this task will start within a minute.",
        )
    )
    await session.commit()
    await session.refresh(schedule)
    await publish_message_changed(topic_id)
    await publish_topic_changed(topic_id)
    # Nudge the scheduler so the run fires now instead of waiting for the next
    # poll tick (no-op if the scheduler is disabled).
    await get_scheduler().nudge()
    return schedule


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(topic_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """Delete the scheduled topic entirely (schedule cascades with it)."""
    topic = await session.get(Topic, topic_id)
    if topic is None or topic.kind != "scheduled":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled topic not found")
    await session.delete(topic)
    await session.commit()
    await publish_topic_changed(topic_id)


async def _get_for_topic(session: AsyncSession, topic_id: int) -> TopicSchedule:
    result = await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return schedule
