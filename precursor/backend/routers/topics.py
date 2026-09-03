"""Topic CRUD + tree endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
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
from precursor.backend.services.collections import (
    move_subtree_to_collection,
    resolve_collection_default_role_id,
    resolve_collection_id,
)
from precursor.backend.services.events import (
    publish_message_changed,
    publish_read_changed,
    publish_topic_changed,
    set_current_client_id,
)
from precursor.backend.services.scheduler import get_scheduler
from precursor.backend.services.slugs import allocate_unique_slug, slugify
from precursor.backend.services.topic_issue import (
    create_linked_issue as create_topic_linked_issue,
)
from precursor.backend.services.unread import message_unread_counts

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.get("", response_model=list[TopicRead])
async def list_topics(
    q: str | None = None,
    collection_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Topic]:
    stmt = select(Topic).where(Topic.archived_at.is_(None)).order_by(Topic.updated_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Topic.title.ilike(like))
    if collection_id is not None:
        stmt = stmt.where(Topic.collection_id == collection_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/archived", response_model=list[TopicRead])
async def list_archived_topics(
    collection_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Topic]:
    """Flat list of archived topics, most recently archived first."""
    stmt = select(Topic).where(Topic.archived_at.is_not(None)).order_by(Topic.archived_at.desc())
    if collection_id is not None:
        stmt = stmt.where(Topic.collection_id == collection_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/tree", response_model=list[TopicNode])
async def topic_tree(
    collection_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[TopicNode]:
    """Return topics arranged as a tree (roots with nested children).

    Archived topics are skipped; any non-archived descendants of an archived
    node are re-parented to that node's nearest non-archived ancestor (or
    promoted to the root) so they remain reachable in the visible tree.

    When ``collection_id`` is given the tree is restricted to that collection.
    Membership cascades down the tree, so a subtree is never split — filtering
    on the node alone is enough.
    """
    result = await session.execute(select(Topic).options(selectinload(Topic.children)))
    all_topics = list(result.scalars().unique().all())
    by_id = {t.id: t for t in all_topics}

    visible = [t for t in all_topics if t.archived_at is None]
    if collection_id is not None:
        visible = [t for t in visible if t.collection_id == collection_id]
    visible_ids = {t.id for t in visible}

    def visible_parent(t: Topic) -> int | None:
        pid = t.parent_id
        while pid is not None:
            parent = by_id.get(pid)
            if parent is None:
                return None
            if parent.id in visible_ids:
                return parent.id
            pid = parent.parent_id
        return None

    effective_parent: dict[int, int | None] = {t.id: visible_parent(t) for t in visible}
    children_of: dict[int | None, list[Topic]] = {}
    for t in visible:
        children_of.setdefault(effective_parent[t.id], []).append(t)

    # One query for all unread counts: non-user messages newer than the topic's
    # last_read_at. Topics with last_read_at IS NULL are treated as fully read.
    unread_map = await message_unread_counts(session, Topic, Message.topic_id)

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
            public_id=node.public_id,
            title=node.title,
            kind=node.kind,
            description=node.description,
            parent_id=node.parent_id,
            collection_id=node.collection_id,
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
    parent: Topic | None = None
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

    # A subtree always lives in one collection: inherit the parent's, otherwise
    # honour the requested one (falling back to the default).
    if parent is not None:
        data["collection_id"] = parent.collection_id
    else:
        data["collection_id"] = await resolve_collection_id(session, payload.collection_id)

    # When no role is picked, a new topic inherits its collection's default
    # Assistant Role (null leaves it on the built-in default role).
    if data.get("role_id") is None:
        data["role_id"] = await resolve_collection_default_role_id(session, data["collection_id"])

    if create_linked_issue:
        # Create the issue first so a GitHub failure aborts before the topic is
        # persisted, keeping topic and issue in sync.
        repo, issue_number = await create_topic_linked_issue(
            session,
            parent_id=payload.parent_id,
            title=payload.title,
            description=payload.description,
            repo_override=payload.github_repo,
            collection_id=data["collection_id"],
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


@router.get("/by-public-id/{public_id}", response_model=TopicRead)
async def get_topic_by_public_id(
    public_id: str, session: AsyncSession = Depends(get_session)
) -> Topic:
    """Resolve the immutable `/t/<public_id>` permalink.

    The readable URL embeds the collection slug and the ancestor chain, so it
    changes whenever the topic moves. This one doesn't — the SPA resolves it,
    then rewrites the address bar to the readable URL.
    """
    result = await session.execute(select(Topic).where(Topic.public_id == public_id))
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

    # Collection membership cascades: a subtree always lives in exactly one
    # collection, so the two fields that can break that invariant are resolved
    # against each other here. Callers (the settings panel) send both on every
    # save, so *what changed* decides — not field precedence.
    new_parent_id = data.get("parent_id", topic.parent_id)
    new_parent: Topic | None = None
    if new_parent_id is not None:
        new_parent = await session.get(Topic, new_parent_id)
        if new_parent is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_id does not exist")

    requested_collection_id: int | None = None
    if "collection_id" in data:
        requested_collection_id = await resolve_collection_id(session, data["collection_id"])
    data.pop("collection_id", None)

    parent_changed = "parent_id" in data and data["parent_id"] != topic.parent_id
    collection_changed = (
        requested_collection_id is not None and requested_collection_id != topic.collection_id
    )

    target_collection_id: int | None
    if parent_changed and new_parent is not None:
        # Re-parenting is the stronger intent: adopt the new parent's collection
        # so the topic joins the subtree it was just dropped into.
        target_collection_id = new_parent.collection_id
    elif collection_changed:
        # An explicit move. A child can't follow without splitting the subtree
        # it came from, so promote it to a root instead.
        if new_parent is not None and new_parent.collection_id != requested_collection_id:
            data["parent_id"] = None
        target_collection_id = requested_collection_id
    elif new_parent is not None:
        target_collection_id = new_parent.collection_id
    else:
        # A legacy row may still be collection-less; anchor it on the default.
        target_collection_id = await resolve_collection_id(session, topic.collection_id)

    for key, value in data.items():
        setattr(topic, key, value)
    if target_collection_id is not None and target_collection_id != topic.collection_id:
        await session.flush()
        await move_subtree_to_collection(session, topic_id, target_collection_id)

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


@router.post("/{topic_id}/unread", status_code=status.HTTP_204_NO_CONTENT)
async def mark_topic_unread(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    latest = await session.scalar(
        select(Message.created_at)
        .where(Message.topic_id == topic_id)
        .where(Message.role != MessageRole.USER)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    topic.last_read_at = (latest or datetime.now(UTC)) - timedelta(microseconds=1)
    await session.commit()
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
        clear_context=payload.clear_context,
        status="idle",
    )
    # Seeds the primary columns + extra_rules from the resolved rule set, so a
    # legacy flat payload and a multi-rule one land the same way.
    schedule.set_recurrence_rules(payload.resolved_rules() or [])
    schedule.next_run_at = schedule.next_run_after(_now()) if payload.enabled else None
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
    if "clear_context" in data and data["clear_context"] is not None:
        schedule.clear_context = data["clear_context"]

    # Cadence is tri-state per field: omitted = unchanged, value = set, and for
    # run_at_minute an explicit null = back to interval mode. `resolved_rules`
    # folds the flat fields and the `rules` list into one answer, returning None
    # when the payload doesn't touch the cadence at all.
    rules = payload.merged_rules(schedule.recurrence_rules)
    if rules is not None:
        schedule.set_recurrence_rules(rules)
        if schedule.enabled:
            schedule.next_run_at = schedule.next_run_after(_now())

    if "enabled" in data and data["enabled"] is not None:
        schedule.enabled = data["enabled"]
        if schedule.enabled and schedule.next_run_at is None:
            schedule.next_run_at = schedule.next_run_after(_now())
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
