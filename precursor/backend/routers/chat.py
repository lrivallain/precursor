"""Chat router — list messages, post a new turn, stream the assistant reply over SSE."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import Attachment, Message, MessageRole, Topic
from precursor.backend.routers.deps import get_topic_or_404
from precursor.backend.schemas import ChatRequest, MessageRead, StoppedTurn
from precursor.backend.services.app_settings import (
    resolve_llm_max_input_tokens,
    resolve_llm_max_tool_result_tokens,
    resolve_llm_model,
    resolve_llm_reasoning_effort,
    resolve_max_tool_rounds,
)
from precursor.backend.services.events import publish_message_changed
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.message_paging import list_message_window
from precursor.backend.services.note_drafts import consume_note_draft_attachments_to_message
from precursor.backend.services.turn_engine import (
    build_system_context,
    hydrate_history,
    lifecycle_stream,
    load_enabled_mcp_servers,
    run_message_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topics/{topic_id}/messages", tags=["chat"])


@router.get("", response_model=list[MessageRead], dependencies=[Depends(get_topic_or_404)])
async def list_messages(
    topic_id: int,
    limit: int | None = None,
    before_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    """List a topic's transcript, optionally as a cursor-paginated window.

    With no ``limit``/``before_id`` the full transcript is returned in
    chronological order (the historical behaviour). When ``limit`` is given the
    most recent ``limit`` messages are returned; pass the oldest loaded id as
    ``before_id`` to page further back. Either way the slice comes back oldest
    first so the client can append it in render order.
    """
    return await list_message_window(
        session, Message.topic_id, topic_id, limit=limit, before_id=before_id
    )


@router.delete(
    "", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(get_topic_or_404)]
)
async def clear_messages(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Wipe the chat transcript for a topic. Topic + GitHub link are kept."""
    await session.execute(delete(Message).where(Message.topic_id == topic_id))
    await session.commit()
    await publish_message_changed(topic_id)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    topic_id: int,
    message_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard-delete a single message. Attachments cascade with the row."""
    msg = await session.get(Message, message_id)
    if msg is None or msg.topic_id != topic_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    await session.delete(msg)
    await session.commit()
    await publish_message_changed(topic_id)


@router.post("/stopped", response_model=MessageRead)
async def save_stopped_turn(
    topic_id: int,
    payload: StoppedTurn,
    session: AsyncSession = Depends(get_session),
) -> Message:
    """Persist the partial assistant reply when the user stops generation.

    The streaming endpoint only saves the final turn, which never runs once the
    client disconnects. This lets the client keep the text it already received
    instead of losing it on stop.
    """
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    msg = Message(
        topic_id=topic_id,
        role=MessageRole.ASSISTANT,
        content=payload.content,
    )
    session.add(msg)
    await session.commit()
    await publish_message_changed(topic_id)
    # Re-load with attachments eagerly so MessageRead serialization doesn't
    # trigger a lazy load outside the async context.
    result = await session.execute(
        select(Message).where(Message.id == msg.id).options(selectinload(Message.attachments))
    )
    return result.scalar_one()


@router.post("/stream")
async def stream_chat(
    topic_id: int,
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    # Persist the user turn immediately.
    user_msg = Message(topic_id=topic_id, role=MessageRole.USER, content=payload.content)
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)
    await publish_message_changed(topic_id)

    # Bind any pre-uploaded attachments to this user message. We only adopt
    # rows that belong to the same topic and are still unbound, so a stale id
    # from another topic / already-sent turn is silently dropped.
    bound_attachments: list[Attachment] = []
    if payload.attachment_ids:
        att_rows = await session.execute(
            select(Attachment).where(
                Attachment.id.in_(payload.attachment_ids),
                Attachment.topic_id == topic_id,
                Attachment.message_id.is_(None),
            )
        )
        bound_attachments = list(att_rows.scalars().all())
        if bound_attachments:
            await session.execute(
                update(Attachment)
                .where(Attachment.id.in_([a.id for a in bound_attachments]))
                .values(message_id=user_msg.id)
            )
            await session.commit()
            for a in bound_attachments:
                a.message_id = user_msg.id
    if payload.note_attachment_ids:
        note_bound = await consume_note_draft_attachments_to_message(
            session,
            kind="topic",
            container_id=topic_id,
            message_id=user_msg.id,
            attachment_ids=payload.note_attachment_ids,
        )
        if note_bound:
            await session.commit()
            bound_attachments.extend(note_bound)

    # Snapshot history + system context now, before the session closes.
    system_prompt = await build_system_context(session, topic)
    history_result = await session.execute(
        select(Message)
        .where(Message.topic_id == topic_id)
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at)
    )
    history = hydrate_history(list(history_result.scalars().all()))

    # For skill invocations: the persisted user message stays the literal
    # slash command (so the chat UI renders /to-en bravo), but the LLM
    # should see the expanded prompt for this turn only.
    if payload.prompt_override:
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].role == "user":
                history[idx] = ChatMessage(
                    role="user",
                    content=payload.prompt_override,
                    image_urls=history[idx].image_urls,
                )
                break

    enabled_servers = await load_enabled_mcp_servers(session)
    model = payload.model or await resolve_llm_model(session)
    reasoning_effort = await resolve_llm_reasoning_effort(session)
    max_tool_rounds = await resolve_max_tool_rounds(session)
    max_input_tokens = await resolve_llm_max_input_tokens(session)
    max_tool_result_tokens = await resolve_llm_max_tool_result_tokens(session)
    provider = await get_llm_provider(session)
    github_token = await resolve_github_token(session)

    user_echo = {
        "id": user_msg.id,
        "content": user_msg.content,
        "attachments": [
            {
                "id": a.id,
                "topic_id": a.topic_id,
                "message_id": a.message_id,
                "mime": a.mime,
                "size": a.size,
                "original_filename": a.original_filename,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in bound_attachments
        ],
    }

    inner = run_message_stream(
        kind="topic",
        container_id=topic_id,
        system_prompt=system_prompt,
        history=history,
        user_echo=user_echo,
        model=model,
        reasoning_effort=reasoning_effort,
        max_tool_rounds=max_tool_rounds,
        max_input_tokens=max_input_tokens,
        max_tool_result_tokens=max_tool_result_tokens,
        provider=provider,
        github_token=github_token,
        enabled_servers=enabled_servers,
    )
    return EventSourceResponse(lifecycle_stream("topic", topic_id, inner))
