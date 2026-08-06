from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models.collection import Collection
from precursor.backend.models.meeting import MeetingSession
from precursor.backend.models.topic import Topic
from precursor.backend.services.slugs import allocate_unique_slug, slugify


async def seed_collection_scope_demo() -> dict[str, Any]:
    """Create demo collections, topics and a live session for collection-scoping."""
    await init_db()

    async with SessionLocal() as session, session.begin():
        collections = {
            "platform": await _ensure_collection(
                session, "Demo - Platform", "Platform rollout", accent="emerald"
            ),
            "operations": await _ensure_collection(
                session, "Demo - Operations", "Incident follow-up", accent="amber"
            ),
        }

        topics = {
            "platform_root": await _ensure_topic(
                session,
                title="Platform launch plan",
                collection=collections["platform"],
            ),
            "operations_root": await _ensure_topic(
                session,
                title="Incident handoff",
                collection=collections["operations"],
            ),
        }
        topics["platform_child"] = await _ensure_topic(
            session,
            title="Release checklist",
            collection=collections["platform"],
            parent=topics["platform_root"],
        )
        topics["operations_child"] = await _ensure_topic(
            session,
            title="Mitigation steps",
            collection=collections["operations"],
            parent=topics["operations_root"],
        )

        await _ensure_meeting_session(session, topic=topics["operations_root"])

        await session.flush()

        return {
            "collections": {name: collection.id for name, collection in collections.items()},
            "topics": {name: topic.id for name, topic in topics.items()},
        }


async def _ensure_collection(
    session: AsyncSession, name: str, description: str, *, accent: str = "sky"
) -> Collection:
    existing = await session.scalar(select(Collection).where(Collection.name == name))
    if existing is not None:
        if existing.accent != accent:
            existing.accent = accent
        return existing

    collection = Collection(
        name=name,
        slug=await allocate_unique_slug(session, slugify(name), Collection),
        description=description,
        accent=accent,
    )
    session.add(collection)
    await session.flush()
    return collection


async def _ensure_topic(
    session: AsyncSession,
    *,
    title: str,
    collection: Collection,
    parent: Topic | None = None,
) -> Topic:
    existing = await session.scalar(
        select(Topic).where(Topic.title == title, Topic.collection_id == collection.id)
    )
    if existing is not None:
        if existing.collection_id != collection.id:
            existing.collection_id = collection.id
        if parent is not None and existing.parent_id != parent.id:
            existing.parent_id = parent.id
        await session.flush()
        return existing

    topic = Topic(
        title=title,
        slug=await allocate_unique_slug(session, slugify(title) or "topic", Topic),
        collection_id=collection.id,
        parent_id=parent.id if parent is not None else None,
    )
    session.add(topic)
    await session.flush()
    return topic


async def _ensure_meeting_session(session: AsyncSession, *, topic: Topic) -> MeetingSession:
    existing = await session.scalar(select(MeetingSession).where(MeetingSession.title == "Collection scope demo"))
    if existing is not None:
        if existing.topic_id != topic.id:
            existing.topic_id = topic.id
        await session.flush()
        return existing

    meeting = MeetingSession(
        title="Collection scope demo",
        slug=await _unique_meeting_slug(session, "collection-scope-demo"),
        topic_id=topic.id,
    )
    session.add(meeting)
    await session.flush()
    return meeting


async def _unique_meeting_slug(session: AsyncSession, base: str) -> str:
    # MeetingSession is outside allocate_unique_slug's constrained TypeVar, so
    # walk candidates directly the way the live router does.
    candidate = base or "session"
    n = 2
    while await session.scalar(select(MeetingSession.id).where(MeetingSession.slug == candidate)) is not None:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


if __name__ == "__main__":
    asyncio.run(seed_collection_scope_demo())
