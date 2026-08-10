"""Collection CRUD endpoints.

A Collection groups topics into a switchable set that filters the sidebar tree.
The seeded ``General`` collection is protected: it cannot be deleted or renamed,
and a second collection named ``General`` cannot be created.

Deleting a collection never deletes topics — the caller picks a destination
collection and every topic is re-homed there.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Collection, Role, Topic
from precursor.backend.models.collection import DEFAULT_COLLECTION_NAME
from precursor.backend.schemas import CollectionCreate, CollectionRead, CollectionUpdate
from precursor.backend.services.collections import (
    get_default_collection,
    topic_counts_by_collection,
)
from precursor.backend.services.slugs import allocate_unique_slug, slugify

router = APIRouter(prefix="/api/collections", tags=["collections"])


async def _validate_role_id(session: AsyncSession, role_id: int | None) -> None:
    """400 if a non-null default role id doesn't point at an existing role."""
    if role_id is None:
        return
    if await session.get(Role, role_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Default role not found")


async def _find_by_name(
    session: AsyncSession, name: str, *, exclude_id: int | None = None
) -> Collection | None:
    """Case-insensitive name lookup (SQLite's UNIQUE index is case-sensitive)."""
    stmt = select(Collection).where(func.lower(Collection.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Collection.id != exclude_id)
    result = await session.execute(stmt)
    return result.scalars().first()


def _to_read(collection: Collection, counts: dict[int, int]) -> CollectionRead:
    return CollectionRead.model_validate(
        {
            **{
                key: getattr(collection, key)
                for key in (
                    "id",
                    "name",
                    "slug",
                    "description",
                    "github_repo",
                    "accent",
                    "icon",
                    "default_role_id",
                    "is_default",
                    "created_at",
                    "updated_at",
                )
            },
            "topic_count": counts.get(collection.id, 0),
        }
    )


@router.get("", response_model=list[CollectionRead])
async def list_collections(session: AsyncSession = Depends(get_session)) -> list[CollectionRead]:
    # Default first, then alphabetical, so the switcher lists it at the top.
    result = await session.execute(
        select(Collection).order_by(Collection.is_default.desc(), func.lower(Collection.name))
    )
    counts = await topic_counts_by_collection(session)
    return [_to_read(collection, counts) for collection in result.scalars().all()]


@router.post("", response_model=CollectionRead, status_code=status.HTTP_201_CREATED)
async def create_collection(
    payload: CollectionCreate, session: AsyncSession = Depends(get_session)
) -> CollectionRead:
    if payload.name.lower() == DEFAULT_COLLECTION_NAME.lower():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{DEFAULT_COLLECTION_NAME}' is reserved for the built-in collection.",
        )
    if await _find_by_name(session, payload.name):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A collection named '{payload.name}' already exists."
        )
    await _validate_role_id(session, payload.default_role_id)
    collection = Collection(
        name=payload.name,
        slug=await allocate_unique_slug(session, slugify(payload.name), Collection),
        description=payload.description,
        github_repo=payload.github_repo,
        accent=payload.accent,
        icon=payload.icon,
        default_role_id=payload.default_role_id,
        is_default=False,
    )
    session.add(collection)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A collection named '{payload.name}' already exists."
        ) from exc
    await session.refresh(collection)
    return _to_read(collection, {})


@router.get("/{collection_id}", response_model=CollectionRead)
async def get_collection(
    collection_id: int, session: AsyncSession = Depends(get_session)
) -> CollectionRead:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    return _to_read(collection, await topic_counts_by_collection(session))


@router.patch("/{collection_id}", response_model=CollectionRead)
async def update_collection(
    collection_id: int,
    payload: CollectionUpdate,
    session: AsyncSession = Depends(get_session),
) -> CollectionRead:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")

    data = payload.model_dump(exclude_unset=True)
    if "default_role_id" in data:
        await _validate_role_id(session, data["default_role_id"])
    if "name" in data and data["name"] != collection.name:
        if collection.is_default:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "The default collection cannot be renamed."
            )
        if data["name"].lower() == DEFAULT_COLLECTION_NAME.lower():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"'{DEFAULT_COLLECTION_NAME}' is reserved for the built-in collection.",
            )
        if await _find_by_name(session, data["name"], exclude_id=collection_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A collection named '{data['name']}' already exists.",
            )
        collection.slug = await allocate_unique_slug(
            session, slugify(data["name"]), Collection, exclude_id=collection_id
        )
    for key, value in data.items():
        setattr(collection, key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A collection with that name already exists."
        ) from exc
    await session.refresh(collection)
    return _to_read(collection, await topic_counts_by_collection(session))


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: int,
    reassign_to: int | None = Query(
        None, description="Collection to move the topics into (defaults to the built-in one)."
    ),
    session: AsyncSession = Depends(get_session),
) -> None:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    if collection.is_default:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The default collection cannot be deleted."
        )

    if reassign_to is not None:
        if reassign_to == collection_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Topics cannot be reassigned to the collection being deleted.",
            )
        destination = await session.get(Collection, reassign_to)
        if destination is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Destination collection not found")
    else:
        destination = await get_default_collection(session)
        if destination is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No destination collection available.")

    # Move the topics explicitly rather than relying on the DB FK: SQLite does
    # not enforce ON DELETE SET NULL unless the foreign_keys pragma is on, and
    # this codebase manages such cleanups in the API layer (see roles.delete).
    await session.execute(
        update(Topic)
        .where(Topic.collection_id == collection_id)
        .values(collection_id=destination.id)
    )
    await session.delete(collection)
    await session.commit()
