"""Topic schedule and one-shot reminder tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    Collection,
    Reminder,
    Topic,
    TopicSchedule,
)
from precursor.backend.schemas.schedule import RecurrenceRule
from precursor.backend.services.collections import (
    resolve_collection_default_role,
    resolve_collection_id,
)
from precursor.backend.services.slugs import allocate_unique_slug, slugify

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import (
    _GATED,
    _MAX_ROWS,
    _now,
    _reminder_dict,
    _schedule_dict,
    _section_enabled,
    mcp,
)


# --------------------------------------------------------------------------
# schedules
# --------------------------------------------------------------------------
@mcp.tool()
async def list_schedules() -> dict[str, Any]:
    """List recurring scheduled topics and their run state."""
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(TopicSchedule).order_by(TopicSchedule.next_run_at)))
            .scalars()
            .all()
        )
    return {"schedules": [_schedule_dict(s) for s in rows], "count": len(rows)}


@mcp.tool()
async def get_schedule(topic_id: int) -> dict[str, Any]:
    """Get the schedule (recurrence + run state) for a scheduled topic."""
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        s = (
            await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
        ).scalar_one_or_none()
    if s is None:
        return {"error": f"No schedule for topic {topic_id}"}
    return _schedule_dict(s)


@mcp.tool()
async def create_schedule(
    title: str,
    prompt: str,
    interval_seconds: int,
    days_of_week: int = 127,
    run_at_minute: int | None = None,
    timezone: str = "UTC",
    clear_context: bool = False,
    enabled: bool = True,
    collection: str | None = None,
    extra_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a recurring schedule on a new topic that runs ``prompt`` on a cadence.

    ``interval_seconds`` is the minimum gap between runs (>= 60).
    ``days_of_week`` is a 7-bit mask (Mon=1 ... Sun=64; 127 = every day).
    ``run_at_minute`` (0-1439) pins a daily time in ``timezone``; omit for a
    pure interval. When ``clear_context`` is set each run starts fresh.
    ``collection`` names (or slugs) the collection the new topic lands in;
    omit it to use the default one.

    ``extra_rules`` adds further recurrences alongside the primary one — each is
    a dict with the same ``interval_seconds`` / ``days_of_week`` /
    ``run_at_minute`` / ``timezone`` keys. The topic then runs at whichever comes
    first, so "every day at 07:00" plus "every weekday at 12:00" gives both.
    """
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    if not title.strip() or not prompt.strip():
        return {"error": "title and prompt must not be empty"}
    if interval_seconds < 60:
        return {"error": "interval_seconds must be >= 60"}
    try:
        rules = [
            RecurrenceRule(
                interval_seconds=interval_seconds,
                days_of_week=days_of_week,
                run_at_minute=run_at_minute,
                timezone=timezone,
            ),
            *(RecurrenceRule(**rule) for rule in extra_rules or []),
        ]
    except (TypeError, ValidationError) as exc:
        return {"error": f"Invalid extra_rules: {exc}"}
    async with SessionLocal() as session:
        collection_id: int | None = None
        if collection:
            wanted = collection.strip().lower()
            match = (
                (
                    await session.execute(
                        select(Collection).where(
                            or_(
                                func.lower(Collection.name) == wanted,
                                func.lower(Collection.slug) == wanted,
                            )
                        )
                    )
                )
                .scalars()
                .first()
            )
            if match is None:
                return {"error": f"Collection '{collection}' not found"}
            collection_id = match.id
        # Never leave the membership null: no collection filter matches null, so
        # the topic would be invisible in the sidebar.
        collection_id = await resolve_collection_id(session, collection_id)
        topic = Topic(
            title=title,
            slug=await allocate_unique_slug(session, slugify(title) or "scheduled-topic", Topic),
            collection_id=collection_id,
            role=await resolve_collection_default_role(session, collection_id),
        )
        session.add(topic)
        await session.flush()
        schedule = TopicSchedule(
            topic_id=topic.id,
            enabled=enabled,
            prompt=prompt,
            clear_context=clear_context,
            status="idle",
        )
        schedule.set_recurrence_rules([rule.to_timing() for rule in rules])
        schedule.next_run_at = schedule.next_run_after(_now()) if enabled else None
        session.add(schedule)
        await session.commit()
        await session.refresh(schedule)
        result = _schedule_dict(schedule)
    return result


@mcp.tool()
async def set_schedule_enabled(topic_id: int, enabled: bool) -> dict[str, Any]:
    """Enable or pause a scheduled topic. Recomputes the next run when enabling."""
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        s = (
            await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
        ).scalar_one_or_none()
        if s is None:
            return {"error": f"No schedule for topic {topic_id}"}
        s.enabled = enabled
        s.next_run_at = s.next_run_after(_now()) if enabled else None
        await session.commit()
        await session.refresh(s)
        result = _schedule_dict(s)
    return result


@mcp.tool()
async def run_schedule_now(topic_id: int) -> dict[str, Any]:
    """Pull a scheduled topic's next run forward so the ticker fires it soon.

    The background scheduler runs in the main app process and will pick this up
    on its next poll (within the poll interval).
    """
    if not await _section_enabled("schedules"):
        return {"error": _GATED.format(section="schedules")}
    async with SessionLocal() as session:
        s = (
            await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
        ).scalar_one_or_none()
        if s is None:
            return {"error": f"No schedule for topic {topic_id}"}
        if s.status == "running":
            return {"error": "Run already in progress"}
        s.enabled = True
        s.next_run_at = _now()
        s.status = "idle"
        s.lease_until = None
        s.last_error = None
        await session.commit()
        await session.refresh(s)
        result = _schedule_dict(s)
    return result


# --------------------------------------------------------------------------
# reminders (write — one-shot topic reminders)
# --------------------------------------------------------------------------
def _parse_remind_at(value: str) -> datetime:
    """Parse an ISO 8601 ``remind_at`` string into an aware UTC datetime.

    Accepts a trailing ``Z`` and naive strings (assumed UTC), so callers can
    pass either ``2026-07-20T09:00:00Z`` or ``2026-07-20T09:00:00+02:00``.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@mcp.tool()
async def list_reminders() -> dict[str, Any]:
    """List one-shot reminders set on topics, soonest first.

    Includes both ``scheduled`` (waiting) and ``fired`` (due, awaiting
    acknowledgment) reminders. Reminders attached to chats are omitted — this
    server is topic-scoped.
    """
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Reminder)
                    .where(Reminder.topic_id.is_not(None))
                    .order_by(Reminder.remind_at)
                    .limit(_MAX_ROWS)
                )
            )
            .scalars()
            .all()
        )
    return {"reminders": [_reminder_dict(r) for r in rows], "count": len(rows)}


@mcp.tool()
async def get_reminder(topic_id: int) -> dict[str, Any]:
    """Get the one-shot reminder set on a topic, if any."""
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    from precursor.backend.services import reminders as reminder_service

    async with SessionLocal() as session:
        reminder = await reminder_service.get_reminder(session, "topic", topic_id)
    if reminder is None:
        return {"error": f"No reminder set for topic {topic_id}"}
    return _reminder_dict(reminder)


@mcp.tool()
async def set_reminder(topic_id: int, remind_at: str, note: str | None = None) -> dict[str, Any]:
    """Set (or replace) a one-shot reminder on a topic.

    ``remind_at`` is an ISO 8601 timestamp (e.g. ``2026-07-20T09:00:00Z``);
    naive values are treated as UTC. At that time the topic resurfaces with a
    posted system message. ``note`` is optional free text (max 2000 chars).
    A topic holds at most one reminder, so this replaces any existing one.
    """
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    from precursor.backend.schemas.reminder import ReminderCreate
    from precursor.backend.services import reminders as reminder_service
    from precursor.backend.services.reminder_ticker import get_reminder_ticker

    try:
        parsed = _parse_remind_at(remind_at)
    except ValueError:
        return {"error": f"remind_at is not a valid ISO 8601 datetime: {remind_at!r}"}
    try:
        payload = ReminderCreate(remind_at=parsed, note=note)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        if not await reminder_service.container_exists(session, "topic", topic_id):
            return {"error": f"Topic {topic_id} not found"}
        reminder = await reminder_service.set_reminder(
            session, "topic", topic_id, remind_at=payload.remind_at, note=payload.note
        )
        result = _reminder_dict(reminder)
    # Fire promptly for near/past times instead of waiting for the next poll.
    # A no-op unless the ticker is running in this process (in-app HTTP host).
    await get_reminder_ticker().nudge()
    return result


@mcp.tool()
async def cancel_reminder(topic_id: int) -> dict[str, Any]:
    """Cancel (or acknowledge) a topic's reminder, deleting it.

    Works on both ``scheduled`` and ``fired`` reminders. Returns an error if the
    topic has no reminder.
    """
    if not await _section_enabled("reminders"):
        return {"error": _GATED.format(section="reminders")}
    from precursor.backend.services import reminders as reminder_service

    async with SessionLocal() as session:
        deleted = await reminder_service.delete_reminder(session, "topic", topic_id)
    if not deleted:
        return {"error": f"No reminder set for topic {topic_id}"}
    return {"topic_id": topic_id, "deleted": True}
