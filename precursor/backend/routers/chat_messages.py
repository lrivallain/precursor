"""Chat message endpoints — list, delete, and stream turns for flat chat sessions.

Mirrors the topic chat router but targets ``Chat`` containers (no GitHub issue
context, no attachments). The heavy streaming logic is shared via
``_run_message_stream`` in :mod:`precursor.backend.routers.chat`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import Chat, Message, MessageRole
from precursor.backend.routers.chat import (
    _build_chat_system_context,
    _hydrate_history,
    _lifecycle_stream,
    _load_enabled_mcp_servers,
    _run_message_stream,
)
from precursor.backend.schemas import ChatRequest, MessageRead, StoppedTurn
from precursor.backend.services.app_settings import (
    resolve_llm_max_input_tokens,
    resolve_llm_max_tool_result_tokens,
    resolve_llm_model,
    resolve_max_tool_rounds,
)
from precursor.backend.services.events import publish_message_changed_chat
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats/{chat_id}/messages", tags=["chat"])


@router.get("", response_model=list[MessageRead])
async def list_messages(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    if await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    result = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at)
    )
    return list(result.scalars().all())


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Wipe the transcript for a chat. The chat row itself is kept."""
    if await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    await session.execute(delete(Message).where(Message.chat_id == chat_id))
    await session.commit()
    await publish_message_changed_chat(chat_id)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    chat_id: int,
    message_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard-delete a single message."""
    msg = await session.get(Message, message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    await session.delete(msg)
    await session.commit()
    await publish_message_changed_chat(chat_id)


@router.post("/stopped", response_model=MessageRead)
async def save_stopped_turn(
    chat_id: int,
    payload: StoppedTurn,
    session: AsyncSession = Depends(get_session),
) -> Message:
    """Persist the partial assistant reply when the user stops generation."""
    if await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    msg = Message(
        chat_id=chat_id,
        role=MessageRole.ASSISTANT,
        content=payload.content,
    )
    session.add(msg)
    await session.commit()
    await publish_message_changed_chat(chat_id)
    result = await session.execute(
        select(Message).where(Message.id == msg.id).options(selectinload(Message.attachments))
    )
    return result.scalar_one()


@router.post("/stream")
async def stream_chat(
    chat_id: int,
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")

    # Persist the user turn immediately.
    user_msg = Message(chat_id=chat_id, role=MessageRole.USER, content=payload.content)
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)
    await publish_message_changed_chat(chat_id)

    # Snapshot history + system context now, before the session closes.
    system_prompt = await _build_chat_system_context(session, chat)
    history_result = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at)
    )
    history = _hydrate_history(list(history_result.scalars().all()))

    # For skill invocations: the persisted user message stays the literal
    # slash command, but the LLM should see the expanded prompt this turn.
    if payload.prompt_override:
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].role == "user":
                history[idx] = ChatMessage(
                    role="user",
                    content=payload.prompt_override,
                    image_urls=history[idx].image_urls,
                )
                break

    enabled_servers = await _load_enabled_mcp_servers(session)
    model = payload.model or await resolve_llm_model(session)
    max_tool_rounds = await resolve_max_tool_rounds(session)
    max_input_tokens = await resolve_llm_max_input_tokens(session)
    max_tool_result_tokens = await resolve_llm_max_tool_result_tokens(session)
    provider = await get_llm_provider(session)
    github_token = await resolve_github_token(session)

    user_echo = {
        "id": user_msg.id,
        "content": user_msg.content,
        "attachments": [],
    }

    inner = _run_message_stream(
        kind="chat",
        container_id=chat_id,
        system_prompt=system_prompt,
        history=history,
        user_echo=user_echo,
        model=model,
        max_tool_rounds=max_tool_rounds,
        max_input_tokens=max_input_tokens,
        max_tool_result_tokens=max_tool_result_tokens,
        provider=provider,
        github_token=github_token,
        enabled_servers=enabled_servers,
    )
    return EventSourceResponse(_lifecycle_stream("chat", chat_id, inner))
