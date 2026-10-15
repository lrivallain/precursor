"""Artifacts router — workspace files attached to topics and chats.

``/api/topics/{id}/artifacts`` and ``/api/chats/{id}/artifacts`` manage one
conversation's files; ``/api/workspaces/{id}/discussions`` lists the topics and
chats attaching files of a workspace (the "continue discussion" list).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import get_session
from precursor.backend.models import Chat, Topic, Workspace
from precursor.backend.routers.deps import get_chat_or_404, get_topic_or_404
from precursor.backend.schemas.artifact import ArtifactCreate, ArtifactRead, DiscussionItem
from precursor.backend.services import artifacts as artifacts_service
from precursor.backend.services.turn_engine import publish_container_changed

router = APIRouter(prefix="/api", tags=["artifacts"])


@router.get("/topics/{topic_id}/artifacts", response_model=list[ArtifactRead])
async def list_topic_artifacts(
    topic: Topic = Depends(get_topic_or_404), session: AsyncSession = Depends(get_session)
) -> list[ArtifactRead]:
    return await artifacts_service.list_artifacts(session, "topic", topic.id)


@router.post(
    "/topics/{topic_id}/artifacts", response_model=ArtifactRead, status_code=status.HTTP_201_CREATED
)
async def attach_topic_artifact(
    payload: ArtifactCreate,
    topic: Topic = Depends(get_topic_or_404),
    session: AsyncSession = Depends(get_session),
) -> ArtifactRead:
    return await _attach(session, "topic", topic.id, payload)


@router.delete("/topics/{topic_id}/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_topic_artifact(
    artifact_id: int,
    topic: Topic = Depends(get_topic_or_404),
    session: AsyncSession = Depends(get_session),
) -> None:
    await artifacts_service.detach(session, "topic", topic.id, artifact_id)
    await publish_container_changed("topic", topic.id)


@router.get("/chats/{chat_id}/artifacts", response_model=list[ArtifactRead])
async def list_chat_artifacts(
    chat: Chat = Depends(get_chat_or_404), session: AsyncSession = Depends(get_session)
) -> list[ArtifactRead]:
    return await artifacts_service.list_artifacts(session, "chat", chat.id)


@router.post(
    "/chats/{chat_id}/artifacts", response_model=ArtifactRead, status_code=status.HTTP_201_CREATED
)
async def attach_chat_artifact(
    payload: ArtifactCreate,
    chat: Chat = Depends(get_chat_or_404),
    session: AsyncSession = Depends(get_session),
) -> ArtifactRead:
    return await _attach(session, "chat", chat.id, payload)


@router.delete("/chats/{chat_id}/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_chat_artifact(
    artifact_id: int,
    chat: Chat = Depends(get_chat_or_404),
    session: AsyncSession = Depends(get_session),
) -> None:
    await artifacts_service.detach(session, "chat", chat.id, artifact_id)
    await publish_container_changed("chat", chat.id)


@router.get("/workspaces/{workspace_id}/discussions", response_model=list[DiscussionItem])
async def list_workspace_discussions(
    workspace_id: int,
    path: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[DiscussionItem]:
    """Discussions attaching files of the workspace; the ones on ``path`` come first."""
    if await session.get(Workspace, workspace_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return await artifacts_service.list_discussions(session, workspace_id, path)


async def _attach(
    session: AsyncSession,
    kind: artifacts_service.ContainerKind,
    container_id: int,
    payload: ArtifactCreate,
) -> ArtifactRead:
    artifact = await artifacts_service.attach(
        session,
        kind,
        container_id,
        workspace_id=payload.workspace_id,
        path=payload.path,
        source=payload.source,
    )
    await publish_container_changed(kind, container_id)
    ws = await session.get(Workspace, artifact.workspace_id)
    assert ws is not None
    return artifacts_service.to_read(artifact, ws)
