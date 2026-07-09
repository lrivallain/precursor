"""Topic CRUD + tree endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.db import get_session
from precursor.backend.models import Message, MessageRole, Topic, TopicSchedule
from precursor.backend.schemas import TopicCreate, TopicNode, TopicRead, TopicUpdate
from precursor.backend.schemas.schedule import (
    ScheduleRead,
    ScheduleSummary,
    ScheduleUpdate,
    TopicScheduleCreate,
)
from precursor.backend.services.events import (
    publish_message_changed,
    publish_read_changed,
    publish_topic_changed,
    set_current_client_id,
)
from precursor.backend.services.schedule_timing import compute_next_run
from precursor.backend.services.scheduler import get_scheduler
from precursor.backend.services.slugs import allocate_unique_slug, slugify
from precursor.backend.services.topic_issue import (
    create_linked_issue as create_topic_linked_issue,
)

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.get("", response_model=list[TopicRead])
async def list_topics(
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Topic]:
    stmt = select(Topic).where(Topic.archived_at.is_(None)).order_by(Topic.updated_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Topic.title.ilike(like))
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/archived", response_model=list[TopicRead])
async def list_archived_topics(
    session: AsyncSession = Depends(get_session),
) -> list[Topic]:
    """Flat list of archived topics, most recently archived first."""
    result = await session.execute(
        select(Topic).where(Topic.archived_at.is_not(None)).order_by(Topic.archived_at.desc())
    )
    return list(result.scalars().all())


@router.get("/tree", response_model=list[TopicNode])
async def topic_tree(session: AsyncSession = Depends(get_session)) -> list[TopicNode]:
    """Return topics arranged as a tree (roots with nested children).

    Archived topics are skipped; any non-archived descendants of an archived
    node are re-parented to that node's nearest non-archived ancestor (or
    promoted to the root) so they remain reachable in the visible tree.
    """
    result = await session.execute(select(Topic).options(selectinload(Topic.children)))
    all_topics = list(result.scalars().unique().all())
    by_id = {t.id: t for t in all_topics}

    def visible_parent(t: Topic) -> int | None:
        pid = t.parent_id
        while pid is not None:
            parent = by_id.get(pid)
            if parent is None:
                return None
            if parent.archived_at is None:
                return parent.id
            pid = parent.parent_id
        return None

    visible = [t for t in all_topics if t.archived_at is None]
    effective_parent: dict[int, int | None] = {t.id: visible_parent(t) for t in visible}
    children_of: dict[int | None, list[Topic]] = {}
    for t in visible:
        children_of.setdefault(effective_parent[t.id], []).append(t)

    # One query for all unread counts: non-user messages newer than the topic's
    # last_read_at. Topics with last_read_at IS NULL are treated as fully read.
    unread_rows = await session.execute(
        select(Topic.id, func.count(Message.id))
        .join(Message, Message.topic_id == Topic.id)
        .where(Topic.last_read_at.is_not(None))
        .where(Message.role != MessageRole.USER)
        .where(Message.created_at > Topic.last_read_at)
        .group_by(Topic.id)
    )
    unread_map: dict[int, int] = {row[0]: row[1] for row in unread_rows.all()}

    # Schedule summaries for scheduled topics, keyed by topic id.
    schedule_rows = await session.execute(select(TopicSchedule))
    schedule_map: dict[int, ScheduleSummary] = {
        s.topic_id: ScheduleSummary.model_validate(s) for s in schedule_rows.scalars().all()
    }

    def build(node: Topic) -> TopicNode:
        children = sorted(children_of.get(node.id, []), key=lambda c: c.title.lower())
        return TopicNode(
            id=node.id,
            slug=node.slug,
            title=node.title,
            kind=node.kind,
            description=node.description,
            parent_id=node.parent_id,
            github_repo=node.github_repo,
            github_issue_number=node.github_issue_number,
            pinned=node.pinned,
            archived_at=node.archived_at,
            created_at=node.created_at,
            updated_at=node.updated_at,
            children=[build(c) for c in children],
            unread_count=unread_map.get(node.id, 0),
            schedule=schedule_map.get(node.id),
        )

    roots = sorted(children_of.get(None, []), key=lambda t: t.title.lower())
    return [build(r) for r in roots]


@router.post("", response_model=TopicRead, status_code=status.HTTP_201_CREATED)
async def create_topic(
    payload: TopicCreate,
    session: AsyncSession = Depends(get_session),
) -> Topic:
    if payload.parent_id is not None:
        parent = await session.get(Topic, payload.parent_id)
        if parent is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_id does not exist")

    data = payload.model_dump()
    create_linked_issue = data.pop("create_linked_issue", False)
    requested_slug = data.pop("slug", None)
    base = slugify(requested_slug) if requested_slug else slugify(payload.title)
    if not base:
        base = "topic"
    data["slug"] = await allocate_unique_slug(session, base, Topic)

    if create_linked_issue:
        # Create the issue first so a GitHub failure aborts before the topic is
        # persisted, keeping topic and issue in sync.
        repo, issue_number = await create_topic_linked_issue(
            session,
            parent_id=payload.parent_id,
            title=payload.title,
            description=payload.description,
            repo_override=payload.github_repo,
        )
        data["github_repo"] = repo
        data["github_issue_number"] = issue_number

    topic = Topic(**data)
    session.add(topic)
    await session.commit()
    await session.refresh(topic)
    await publish_topic_changed(topic.id)
    return topic


@router.get("/by-slug/{slug}", response_model=TopicRead)
async def get_topic_by_slug(slug: str, session: AsyncSession = Depends(get_session)) -> Topic:
    result = await session.execute(select(Topic).where(Topic.slug == slug))
    topic = result.scalar_one_or_none()
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    return topic


@router.get("/{topic_id}", response_model=TopicRead)
async def get_topic(topic_id: int, session: AsyncSession = Depends(get_session)) -> Topic:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    return topic


@router.patch("/{topic_id}", response_model=TopicRead)
async def update_topic(
    topic_id: int,
    payload: TopicUpdate,
    session: AsyncSession = Depends(get_session),
) -> Topic:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    data = payload.model_dump(exclude_unset=True)
    if "parent_id" in data and data["parent_id"] == topic_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Topic cannot be its own parent")
    if "slug" in data:
        base = slugify(data["slug"] or "")
        if not base:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Slug must contain at least one alphanumeric character",
            )
        data["slug"] = await allocate_unique_slug(session, base, Topic, exclude_id=topic_id)
    for key, value in data.items():
        setattr(topic, key, value)

    await session.commit()
    await session.refresh(topic)
    await publish_topic_changed(topic.id)
    return topic


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(topic_id: int, session: AsyncSession = Depends(get_session)) -> None:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    # Promote direct children one level up (parent's parent, or root) so they
    # are not lost when their parent disappears.
    new_parent_id = topic.parent_id
    await session.execute(
        update(Topic).where(Topic.parent_id == topic_id).values(parent_id=new_parent_id)
    )
    await session.delete(topic)
    await session.commit()
    await publish_topic_changed(topic_id)


@router.post("/{topic_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_topic_read(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Stamp the topic's last_read_at to now, clearing the unread badge."""
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    topic.last_read_at = datetime.now(UTC)
    await session.commit()
    # Let other tabs clear this topic's badge/counter in real time.
    await publish_read_changed(topic_id=topic_id)


@router.post("/{topic_id}/archive", response_model=TopicRead)
async def archive_topic(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> Topic:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    if topic.archived_at is None:
        topic.archived_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(topic)
        await publish_topic_changed(topic_id)
    return topic


@router.post("/{topic_id}/unarchive", response_model=TopicRead)
async def unarchive_topic(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> Topic:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    if topic.archived_at is not None:
        topic.archived_at = None
        await session.commit()
        await session.refresh(topic)
        await publish_topic_changed(topic_id)
    return topic


# --------------------------------------------------------------------- schedule
#
# Any topic can run on a recurrence: it has at most one TopicSchedule holding the
# prompt to send each run + the cadence (see services/scheduler.py). A topic is
# "scheduled" simply when it has an enabled schedule — there is no special topic
# kind. These endpoints mirror the agent schedule surface.


def _now() -> datetime:
    return datetime.now(UTC)


async def _require_topic(session: AsyncSession, topic_id: int) -> Topic:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    return topic


async def _get_schedule_or_404(session: AsyncSession, topic_id: int) -> TopicSchedule:
    result = await session.execute(select(TopicSchedule).where(TopicSchedule.topic_id == topic_id))
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic schedule not found")
    return schedule


@router.get("/{topic_id}/schedule", response_model=ScheduleRead)
async def get_topic_schedule(
    topic_id: int, session: AsyncSession = Depends(get_session)
) -> TopicSchedule:
    await _require_topic(session, topic_id)
    return await _get_schedule_or_404(session, topic_id)


@router.post(
    "/{topic_id}/schedule",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_topic_schedule(
    topic_id: int,
    payload: TopicScheduleCreate,
    session: AsyncSession = Depends(get_session),
) -> TopicSchedule:
    await _require_topic(session, topic_id)
    existing = await session.execute(
        select(TopicSchedule).where(TopicSchedule.topic_id == topic_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Topic already has a schedule")

    schedule = TopicSchedule(
        topic_id=topic_id,
        enabled=payload.enabled,
        prompt=payload.prompt,
        interval_seconds=payload.interval_seconds,
        days_of_week=payload.days_of_week,
        run_at_minute=payload.run_at_minute,
        timezone=payload.timezone,
        clear_context=payload.clear_context,
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
    await publish_topic_changed(topic_id)
    return schedule


@router.patch("/{topic_id}/schedule", response_model=ScheduleRead)
async def update_topic_schedule(
    topic_id: int,
    payload: ScheduleUpdate,
    session: AsyncSession = Depends(get_session),
) -> TopicSchedule:
    schedule = await _get_schedule_or_404(session, topic_id)
    data = payload.model_dump(exclude_unset=True)

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


@router.post("/{topic_id}/schedule/run", response_model=ScheduleRead)
async def run_topic_schedule_now(
    topic_id: int, session: AsyncSession = Depends(get_session)
) -> TopicSchedule:
    """Pull the next run forward so the ticker picks it up immediately."""
    schedule = await _get_schedule_or_404(session, topic_id)
    if schedule.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "Run already in progress")
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
    # Broadcast to all windows (including this one) so the confirmation isn't
    # echo-suppressed in the originating window.
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
    scheduler = get_scheduler()
    scheduler.mark_forced(topic_id)
    await scheduler.nudge()
    return schedule


@router.delete("/{topic_id}/schedule", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic_schedule(
    topic_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    """Remove only the schedule; the topic and its messages are kept."""
    schedule = await _get_schedule_or_404(session, topic_id)
    await session.delete(schedule)
    await session.commit()
    await publish_topic_changed(topic_id)
