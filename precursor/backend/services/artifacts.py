"""Conversation artifacts — workspace files attached to topics and chats.

A file becomes an artifact when the assistant's workspace write tools create or
change it, when the user attaches it by hand, or when a discussion is opened on
it in the workspace. Reads never attach. The link is ``(workspace, path)`` only:
a file renamed or deleted outside the app reads as missing, nothing follows it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    ARTIFACT_SOURCE_ASSISTANT,
    Chat,
    ConversationArtifact,
    Topic,
    Workspace,
)
from precursor.backend.schemas.artifact import ArtifactRead, DiscussionItem, WorkspaceTurnContext
from precursor.backend.services import workspace_fs as fs
from precursor.backend.services.workspaces import browse_root

logger = logging.getLogger(__name__)

ContainerKind = Literal["topic", "chat"]

# Bounds on what rides in a turn's system prompt.
MAX_PROMPT_ARTIFACTS = 40
OPEN_FILE_MAX_CHARS = 60_000


def normalize_path(path: str) -> str:
    """``a//b/`` → ``a/b``: the form artifacts are stored and matched in."""
    return "/".join(seg for seg in (path or "").strip().split("/") if seg)


def _container_column(kind: ContainerKind):  # type: ignore[no-untyped-def]
    return ConversationArtifact.topic_id if kind == "topic" else ConversationArtifact.chat_id


def _container_kwargs(kind: ContainerKind, container_id: int) -> dict[str, int]:
    return {"topic_id": container_id} if kind == "topic" else {"chat_id": container_id}


def _file_exists(ws: Workspace, path: str) -> bool:
    try:
        return fs.safe_join(browse_root(ws), path).is_file()
    except fs.UnsafePathError:
        return False


def to_read(artifact: ConversationArtifact, ws: Workspace) -> ArtifactRead:
    return ArtifactRead(
        id=artifact.id,
        workspace_id=ws.id,
        workspace_slug=ws.slug,
        workspace_name=ws.name,
        path=artifact.path,
        source=artifact.source,
        exists=_file_exists(ws, artifact.path),
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
    )


async def list_artifacts(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> list[ArtifactRead]:
    """The container's artifacts, most recently touched first."""
    rows = await session.execute(
        select(ConversationArtifact, Workspace)
        .join(Workspace, Workspace.id == ConversationArtifact.workspace_id)
        .where(_container_column(kind) == container_id)
        .order_by(ConversationArtifact.updated_at.desc(), ConversationArtifact.id.desc())
    )
    return [to_read(artifact, ws) for artifact, ws in rows.all()]


async def attach(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    *,
    workspace_id: int,
    path: str,
    source: str,
    require_file: bool = True,
) -> ConversationArtifact:
    """Attach ``path`` to the container, or refresh the link when it already exists."""
    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    clean = normalize_path(path)
    if not clean:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A file path is required.")
    try:
        fs.safe_join(browse_root(ws), clean)
    except fs.UnsafePathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if require_file and not _file_exists(ws, clean):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File not found: {clean}")

    existing = (
        await session.execute(
            select(ConversationArtifact).where(
                _container_column(kind) == container_id,
                ConversationArtifact.workspace_id == workspace_id,
                ConversationArtifact.path == clean,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(existing)
        return existing

    artifact = ConversationArtifact(
        workspace_id=workspace_id,
        path=clean,
        source=source,
        **_container_kwargs(kind, container_id),
    )
    session.add(artifact)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent attach won the unique constraint: that row is the result.
        await session.rollback()
        return await attach(
            session,
            kind,
            container_id,
            workspace_id=workspace_id,
            path=clean,
            source=source,
            require_file=require_file,
        )
    await session.refresh(artifact)
    return artifact


async def detach(
    session: AsyncSession, kind: ContainerKind, container_id: int, artifact_id: int
) -> None:
    artifact = await session.get(ConversationArtifact, artifact_id)
    if (
        artifact is None
        or _container_column(kind) != _container_column(kind)
        or (getattr(artifact, "topic_id" if kind == "topic" else "chat_id") != container_id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    await session.delete(artifact)
    await session.commit()


async def record_written_file(
    kind: ContainerKind, container_id: int, link: dict[str, Any] | None
) -> None:
    """Attach the file a workspace write tool just changed. Never raises.

    ``link`` is the tool result's ``{"slug", "path", "written"}`` as the turn
    engine extracts it; a read result carries no ``written`` marker.
    """
    if not link or not link.get("written"):
        return
    slug, path = link.get("slug"), link.get("path")
    if not slug or not path:
        return
    try:
        async with SessionLocal() as session:
            ws = (
                await session.execute(select(Workspace).where(Workspace.slug == slug))
            ).scalar_one_or_none()
            if ws is None:
                return
            await attach(
                session,
                kind,
                container_id,
                workspace_id=ws.id,
                path=path,
                source=ARTIFACT_SOURCE_ASSISTANT,
                require_file=True,
            )
    except Exception:
        # A transcript must never lose its tool result over bookkeeping.
        logger.warning("Could not record artifact %s/%s", slug, path, exc_info=True)


async def list_discussions(
    session: AsyncSession, workspace_id: int, path: str | None = None
) -> list[DiscussionItem]:
    """Topics and chats attaching files of the workspace, the open file's first."""
    wanted = normalize_path(path or "")
    rows = (
        (
            await session.execute(
                select(ConversationArtifact)
                .where(ConversationArtifact.workspace_id == workspace_id)
                .order_by(ConversationArtifact.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[tuple[str, int], list[ConversationArtifact]] = {}
    for row in rows:
        key = ("topic", row.topic_id) if row.topic_id is not None else ("chat", row.chat_id or 0)
        grouped.setdefault(key, []).append(row)

    topic_ids = [i for k, i in grouped if k == "topic"]
    chat_ids = [i for k, i in grouped if k == "chat"]
    topics = {
        t.id: t
        for t in (
            await session.execute(
                select(Topic).where(
                    Topic.id.in_(topic_ids),
                    Topic.archived_at.is_(None),
                    Topic.kind != "schedule_root",
                )
            )
        )
        .scalars()
        .all()
    }
    chats = {
        c.id: c
        for c in (
            await session.execute(
                select(Chat).where(Chat.id.in_(chat_ids), Chat.archived_at.is_(None))
            )
        )
        .scalars()
        .all()
    }

    items: list[tuple[DiscussionItem, datetime]] = []
    for (kind, container_id), artifacts in grouped.items():
        paths = [a.path for a in artifacts]
        touched = max(a.updated_at for a in artifacts)
        if kind == "topic":
            topic = topics.get(container_id)
            if topic is None:
                continue
            item = DiscussionItem(
                kind="topic",
                id=topic.id,
                title=topic.title,
                slug=topic.slug,
                public_id=topic.public_id,
                updated_at=topic.updated_at,
                paths=paths,
                has_file=bool(wanted) and wanted in paths,
            )
        else:
            chat = chats.get(container_id)
            if chat is None:
                continue
            item = DiscussionItem(
                kind="chat",
                id=chat.id,
                title=chat.title,
                slug=chat.slug,
                updated_at=chat.updated_at,
                paths=paths,
                has_file=bool(wanted) and wanted in paths,
            )
        items.append((item, touched))
    items.sort(key=lambda pair: (not pair[0].has_file, -pair[1].timestamp()))
    return [item for item, _ in items]


async def artifacts_prompt(session: AsyncSession, kind: ContainerKind, container_id: int) -> str:
    """The files attached to this conversation, as paths only.

    Content is never inlined: the model reads what it needs with the workspace
    tools, which take the ``workspace_id`` named here.
    """
    artifacts = [a for a in await list_artifacts(session, kind, container_id) if a.exists]
    if not artifacts:
        return ""
    lines = [
        "Workspace files attached to this conversation (use the workspace tools with the "
        "given workspace_id to read or change them):"
    ]
    for a in artifacts[:MAX_PROMPT_ARTIFACTS]:
        lines.append(f"- {a.path} (workspace_id: {a.workspace_id}, workspace: {a.workspace_name})")
    if len(artifacts) > MAX_PROMPT_ARTIFACTS:
        lines.append(f"- …and {len(artifacts) - MAX_PROMPT_ARTIFACTS} more")
    return "\n".join(lines)


async def workspace_turn_context(session: AsyncSession, context: WorkspaceTurnContext) -> str:
    """What the model is told when the turn is held from the workspace panel.

    Names the workspace (so it needn't call ``list_workspaces`` first) and
    inlines the file the user has open. Built per turn and never persisted.
    """
    ws = await session.get(Workspace, context.workspace_id)
    if ws is None:
        return ""
    lines = [
        "The user is discussing this from the Files section. Workspace tools (if enabled) "
        "operate on this workspace:",
        f"- workspace_id: {ws.id}",
        f"- slug: {ws.slug}",
        f"- name: {ws.name}",
    ]
    path = normalize_path(context.path or "")
    if path:
        lines.append(f"- the user is currently viewing the file: {path}")
    lines.append(
        "When using workspace filesystem tools, pass this workspace_id and use paths "
        "relative to the workspace root."
    )
    block = "\n".join(lines)
    if path:
        try:
            content = fs.read_text(browse_root(ws), path)
        except (fs.UnsafePathError, FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            return block
        truncated = len(content) > OPEN_FILE_MAX_CHARS
        shown = content[:OPEN_FILE_MAX_CHARS]
        note = "\n…(truncated)" if truncated else ""
        block += f"\n\nIts current content is:\n\n```\n{shown}{note}\n```"
    return block
