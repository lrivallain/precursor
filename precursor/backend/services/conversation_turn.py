"""Turn preparation and transcript upkeep for persisted conversations.

Everything a turn needs before the model runs — the user message and its
attachments, the history snapshot, the resolved LLM settings, the user echo —
is built here, so the topic and chat stream routers and the unattended topic
turn (``services/turn.py``) prepare a turn the same way. Container-specific
policy stays explicit at each call site: a chat's system-prompt mode and
auto-naming, and the scheduler keeping Precursor's own MCP server away from
itself.

Every helper finishes its DB work before returning. The stream endpoints hand
the result to an SSE generator that outlives the request-scoped session, and the
generator persists through fresh sessions of its own.

The transcript endpoints both containers expose (list, clear, delete one, save a
stopped reply) live here too, keyed on :data:`ContainerKind`, so the topic and
chat routers stay one-liners over the same behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from precursor.backend.models import Attachment, Message, MessageRole
from precursor.backend.schemas import ChatRequest
from precursor.backend.services.app_settings import (
    resolve_llm_max_input_tokens,
    resolve_llm_max_tool_result_tokens,
    resolve_llm_model,
    resolve_llm_reasoning_effort,
    resolve_max_tool_rounds,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage, LLMProvider
from precursor.backend.services.message_paging import list_message_window
from precursor.backend.services.note_drafts import consume_note_draft_attachments_to_message
from precursor.backend.services.turn_engine import (
    ContainerKind,
    container_message_kwargs,
    hydrate_history,
    lifecycle_stream,
    load_enabled_mcp_servers,
    prepare_retry_turn,
    publish_container_changed,
    run_message_stream,
)


def _message_fk(kind: ContainerKind) -> InstrumentedAttribute[int | None]:
    return Message.topic_id if kind == "topic" else Message.chat_id


def _attachment_fk(kind: ContainerKind) -> InstrumentedAttribute[int | None]:
    return Attachment.topic_id if kind == "topic" else Attachment.chat_id


# -- Settings --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnSettings:
    """The LLM + MCP settings a turn runs with, resolved once up front."""

    model: str
    reasoning_effort: str
    max_tool_rounds: int
    max_input_tokens: int
    max_tool_result_tokens: int
    provider: LLMProvider
    github_token: str
    enabled_servers: list[str]


async def resolve_turn_settings(
    session: AsyncSession,
    *,
    model_override: str | None = None,
    exclude_servers: Collection[str] = (),
) -> TurnSettings:
    """Resolve every setting a turn needs from the app settings.

    ``model_override`` is the composer's per-turn model pick; the saved model is
    only looked up when it is absent. ``exclude_servers`` removes MCP servers
    from the enabled set for this turn only.
    """
    enabled = [s for s in await load_enabled_mcp_servers(session) if s not in exclude_servers]
    return TurnSettings(
        model=model_override or await resolve_llm_model(session),
        reasoning_effort=await resolve_llm_reasoning_effort(session),
        max_tool_rounds=await resolve_max_tool_rounds(session),
        max_input_tokens=await resolve_llm_max_input_tokens(session),
        max_tool_result_tokens=await resolve_llm_max_tool_result_tokens(session),
        provider=await get_llm_provider(session),
        github_token=await resolve_github_token(session),
        enabled_servers=enabled,
    )


# -- The user turn ---------------------------------------------------------


async def persist_user_message(
    session: AsyncSession, kind: ContainerKind, container_id: int, content: str
) -> Message:
    """Save ``content`` as a user turn now, so the transcript shows it at once."""
    user_msg = Message(
        role=MessageRole.USER,
        content=content,
        **container_message_kwargs(kind, container_id),
    )
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)
    await publish_container_changed(kind, container_id)
    return user_msg


async def persist_user_turn(
    session: AsyncSession, kind: ContainerKind, container_id: int, payload: ChatRequest
) -> tuple[Message, list[Attachment]]:
    """Record the user side of a streamed turn; return it with its attachments.

    A retry replays an existing prompt instead: its message (and attachments) is
    reused and the failed tail dropped, so nothing new is persisted.
    """
    if payload.retry_message_id is not None:
        user_msg = await prepare_retry_turn(
            session, kind=kind, container_id=container_id, message_id=payload.retry_message_id
        )
        return user_msg, list(user_msg.attachments)

    user_msg = await persist_user_message(session, kind, container_id, payload.content)

    # Only adopt rows that belong to this container and are still unbound, so a
    # stale id from another container / already-sent turn is silently dropped.
    bound: list[Attachment] = []
    if payload.attachment_ids:
        rows = await session.execute(
            select(Attachment).where(
                Attachment.id.in_(payload.attachment_ids),
                _attachment_fk(kind) == container_id,
                Attachment.message_id.is_(None),
            )
        )
        bound = list(rows.scalars().all())
        if bound:
            await session.execute(
                update(Attachment)
                .where(Attachment.id.in_([a.id for a in bound]))
                .values(message_id=user_msg.id)
            )
            await session.commit()
            for a in bound:
                a.message_id = user_msg.id
    if payload.note_attachment_ids:
        note_bound = await consume_note_draft_attachments_to_message(
            session,
            kind=kind,
            container_id=container_id,
            message_id=user_msg.id,
            attachment_ids=payload.note_attachment_ids,
        )
        if note_bound:
            await session.commit()
            bound.extend(note_bound)
    return user_msg, bound


def user_echo(
    kind: ContainerKind, user_msg: Message, attachments: list[Attachment]
) -> dict[str, Any]:
    """The ``user_message`` SSE payload: the persisted turn and its attachments."""
    fk = "topic_id" if kind == "topic" else "chat_id"
    return {
        "id": user_msg.id,
        "content": user_msg.content,
        "attachments": [
            {
                "id": a.id,
                fk: getattr(a, fk),
                "message_id": a.message_id,
                "mime": a.mime,
                "size": a.size,
                "original_filename": a.original_filename,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in attachments
        ],
    }


# -- History ---------------------------------------------------------------


async def snapshot_history(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    *,
    prompt_override: str | None = None,
) -> list[ChatMessage]:
    """Load the container's transcript as the model will see it this turn.

    ``prompt_override`` is a skill invocation's expanded prompt: the persisted
    user turn stays the literal slash command (so the transcript renders
    ``/to-en bravo``), but the model sees the expansion for this turn only.
    """
    result = await session.execute(
        select(Message)
        .where(_message_fk(kind) == container_id)
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at)
    )
    history = hydrate_history(list(result.scalars().all()))
    if prompt_override:
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].role == "user":
                history[idx] = ChatMessage(
                    role="user",
                    content=prompt_override,
                    image_urls=history[idx].image_urls,
                )
                break
    return history


# -- Streaming -------------------------------------------------------------


def stream_turn(
    kind: ContainerKind,
    container_id: int,
    *,
    system_prompt: str,
    history: list[ChatMessage],
    echo: dict[str, Any],
    settings: TurnSettings,
) -> AsyncIterator[dict[str, str]]:
    """The SSE event stream for a prepared turn, framed by stream.started/ended."""
    inner = run_message_stream(
        kind=kind,
        container_id=container_id,
        system_prompt=system_prompt,
        history=history,
        user_echo=echo,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        max_tool_rounds=settings.max_tool_rounds,
        max_input_tokens=settings.max_input_tokens,
        max_tool_result_tokens=settings.max_tool_result_tokens,
        provider=settings.provider,
        github_token=settings.github_token,
        enabled_servers=settings.enabled_servers,
    )
    return lifecycle_stream(kind, container_id, inner)


# -- Transcript upkeep -----------------------------------------------------


async def list_container_messages(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    *,
    limit: int | None = None,
    before_id: int | None = None,
) -> list[Message]:
    """A container's transcript, optionally as a cursor-paginated window."""
    return await list_message_window(
        session, _message_fk(kind), container_id, limit=limit, before_id=before_id
    )


async def clear_container_messages(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> None:
    """Wipe a container's transcript. The container itself is kept."""
    await session.execute(delete(Message).where(_message_fk(kind) == container_id))
    await session.commit()
    await publish_container_changed(kind, container_id)


async def delete_container_message(
    session: AsyncSession, kind: ContainerKind, container_id: int, message_id: int
) -> None:
    """Hard-delete one message. Raises 404 unless it belongs to this container."""
    msg = await session.get(Message, message_id)
    owner = None if msg is None else (msg.topic_id if kind == "topic" else msg.chat_id)
    if msg is None or owner != container_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    await session.delete(msg)
    await session.commit()
    await publish_container_changed(kind, container_id)


async def save_stopped_container_turn(
    session: AsyncSession, kind: ContainerKind, container_id: int, content: str
) -> Message:
    """Persist the partial reply the client kept when the user stopped a turn.

    The stream only saves its final turn, which never runs once the client
    disconnects; this keeps the text already received instead of losing it.
    """
    msg = Message(
        role=MessageRole.ASSISTANT,
        content=content,
        **container_message_kwargs(kind, container_id),
    )
    session.add(msg)
    await session.commit()
    await publish_container_changed(kind, container_id)
    # Re-load with attachments eagerly so MessageRead serialization doesn't
    # trigger a lazy load outside the async context.
    result = await session.execute(
        select(Message).where(Message.id == msg.id).options(selectinload(Message.attachments))
    )
    return result.scalar_one()
