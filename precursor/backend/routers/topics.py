"""Topic CRUD + tree endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.db import get_session
from precursor.backend.models import Topic
from precursor.backend.schemas import TopicCreate, TopicNode, TopicRead, TopicUpdate

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.get("", response_model=list[TopicRead])
async def list_topics(
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Topic]:
    stmt = select(Topic).order_by(Topic.updated_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Topic.title.ilike(like))
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/tree", response_model=list[TopicNode])
async def topic_tree(session: AsyncSession = Depends(get_session)) -> list[TopicNode]:
    """Return topics arranged as a tree (roots with nested children)."""
    result = await session.execute(select(Topic).options(selectinload(Topic.children)))
    all_topics = list(result.scalars().unique().all())

    def build(node: Topic) -> TopicNode:
        children = sorted(node.children, key=lambda c: c.title.lower())
        return TopicNode(
            id=node.id,
            title=node.title,
            description=node.description,
            parent_id=node.parent_id,
            github_repo=node.github_repo,
            github_issue_number=node.github_issue_number,
            created_at=node.created_at,
            updated_at=node.updated_at,
            children=[build(c) for c in children],
        )

    roots = [t for t in all_topics if t.parent_id is None]
    roots.sort(key=lambda t: t.title.lower())
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

    topic = Topic(**payload.model_dump())
    session.add(topic)
    await session.commit()
    await session.refresh(topic)
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
    for key, value in data.items():
        setattr(topic, key, value)

    await session.commit()
    await session.refresh(topic)
    return topic


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(topic_id: int, session: AsyncSession = Depends(get_session)) -> None:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    await session.delete(topic)
    await session.commit()
