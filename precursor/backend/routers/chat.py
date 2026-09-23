"""Chat router — list messages, post a new turn, stream the assistant reply over SSE."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import Message, MessageRole, Topic
from precursor.backend.routers.deps import get_topic_or_404
from precursor.backend.schemas import ChatRequest, MessageRead, StoppedTurn
from precursor.backend.services.conversation_turn import (
    persist_user_turn,
    resolve_turn_settings,
    snapshot_history,
    stream_turn,
    user_echo,
)
from precursor.backend.services.events import publish_message_changed
from precursor.backend.services.message_paging import list_message_window
from precursor.backend.services.turn_engine import build_system_context

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


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(get_topic_or_404)])
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

    user_msg, attachments = await persist_user_turn(session, "topic", topic_id, payload)
    # Snapshot history + system context now, before the session closes.
    system_prompt = await build_system_context(session, topic)
    history = await snapshot_history(
        session, "topic", topic_id, prompt_override=payload.prompt_override
    )
    settings = await resolve_turn_settings(session, model_override=payload.model)
    return EventSourceResponse(
        stream_turn(
            "topic",
            topic_id,
            system_prompt=system_prompt,
            history=history,
            echo=user_echo("topic", user_msg, attachments),
            settings=settings,
        )
    )
