"""Collection resolution, subtree cascade and GitHub repo precedence.

A Collection groups topics; membership cascades down the topic tree so a
subtree is never split across collections. Collections also carry an optional
GitHub repo override, giving topics a three-step precedence when resolving
where their issues live: the topic's own ``github_repo`` → its collection's →
the global setting.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Collection, Role, Topic
from precursor.backend.models.collection import DEFAULT_COLLECTION_NAME
from precursor.backend.services.app_settings import resolve_global_github_repo


async def get_default_collection(session: AsyncSession) -> Collection | None:
    """The protected built-in collection, or None on a not-yet-seeded database."""
    result = await session.execute(select(Collection).where(Collection.is_default.is_(True)))
    collection = result.scalars().first()
    if collection is not None:
        return collection
    # Fall back to a name match so a database seeded before `is_default` existed
    # still resolves rather than stranding topics.
    result = await session.execute(
        select(Collection).where(func.lower(Collection.name) == DEFAULT_COLLECTION_NAME.lower())
    )
    return result.scalars().first()


async def resolve_collection_id(session: AsyncSession, collection_id: int | None) -> int | None:
    """Return a usable collection id, falling back to the default.

    A null id — or one pointing at a since-deleted collection — resolves to the
    protected default so topics never end up in limbo.
    """
    if collection_id is not None:
        existing = await session.get(Collection, collection_id)
        if existing is not None:
            return existing.id
    default = await get_default_collection(session)
    return default.id if default else None


async def subtree_ids(session: AsyncSession, root_id: int) -> list[int]:
    """Ids of `root_id` and every descendant, breadth-first.

    Guards against cycles so a corrupted ``parent_id`` chain can't spin forever.
    """
    collected: list[int] = [root_id]
    seen: set[int] = {root_id}
    frontier = [root_id]
    while frontier:
        rows = await session.execute(select(Topic.id).where(Topic.parent_id.in_(frontier)))
        next_frontier = [tid for tid in rows.scalars().all() if tid not in seen]
        seen.update(next_frontier)
        collected.extend(next_frontier)
        frontier = next_frontier
    return collected


async def move_subtree_to_collection(
    session: AsyncSession, root_id: int, collection_id: int | None
) -> list[int]:
    """Assign `collection_id` to `root_id` and all its descendants.

    Returns the affected topic ids. Does not commit — the caller owns the
    transaction so the move lands atomically with whatever else it is changing.
    """
    ids = await subtree_ids(session, root_id)
    await session.execute(
        update(Topic).where(Topic.id.in_(ids)).values(collection_id=collection_id)
    )
    return ids


async def resolve_topic_github_repo(session: AsyncSession, topic: Topic) -> str:
    """Resolve the GitHub repo for `topic`: own override → collection → global.

    Returns "" when nothing is configured at any level, letting callers raise
    their own contextual error.
    """
    own = (topic.github_repo or "").strip()
    if own:
        return own
    if topic.collection_id is not None:
        collection = await session.get(Collection, topic.collection_id)
        if collection is not None:
            inherited = (collection.github_repo or "").strip()
            if inherited:
                return inherited
    return await resolve_global_github_repo(session)


async def resolve_collection_github_repo(session: AsyncSession, collection_id: int | None) -> str:
    """Repo for a collection that has no topic yet: collection → global."""
    if collection_id is not None:
        collection = await session.get(Collection, collection_id)
        if collection is not None:
            own = (collection.github_repo or "").strip()
            if own:
                return own
    return await resolve_global_github_repo(session)


async def resolve_collection_default_role_id(
    session: AsyncSession, collection_id: int | None
) -> int | None:
    """Return the default Assistant Role id for topics created in a collection.

    A null ``collection_id`` — or one pointing at a collection with no default
    role (or a since-deleted role) — yields ``None``, which callers treat as
    "use the built-in default role".
    """
    if collection_id is None:
        return None
    collection = await session.get(Collection, collection_id)
    if collection is None or collection.default_role_id is None:
        return None
    role = await session.get(Role, collection.default_role_id)
    return role.id if role is not None else None


async def topic_counts_by_collection(session: AsyncSession) -> dict[int, int]:
    """Non-archived topic count per collection id."""
    rows = await session.execute(
        select(Topic.collection_id, func.count(Topic.id))
        .where(Topic.archived_at.is_(None), Topic.collection_id.is_not(None))
        .group_by(Topic.collection_id)
    )
    return {cid: count for cid, count in rows.all() if cid is not None}
