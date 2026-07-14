"""Chat message endpoints — list, delete, and stream turns for flat chat sessions.

Mirrors the topic chat router but targets ``Chat`` containers (no GitHub issue
context). The heavy streaming logic is shared via
``_run_message_stream`` in :mod:`precursor.backend.routers.chat`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import (
    Attachment,
    Chat,
    Message,
    MessageRole,
    NoteDraftAttachment,
)
from precursor.backend.routers.chat import (
    _apply_chat_system_prompt,
    _build_chat_system_context,
    _hydrate_history,
    _lifecycle_stream,
    _load_enabled_mcp_servers,
    _run_message_stream,
)
from precursor.backend.routers.commands import _stream_llm
from precursor.backend.routers.deps import get_chat_or_404
from precursor.backend.schemas import (
    ChatRequest,
    MessageRead,
    NoteDraftAttachmentRead,
    NotesAppendRequest,
    NotesAppendResponse,
    NotesDraftResponse,
    NotesDraftSaveRequest,
    NotesRephraseRequest,
    NotesRephraseResponse,
    StoppedTurn,
)
from precursor.backend.services import notes as notes_service
from precursor.backend.services.app_settings import (
    resolve_llm_max_input_tokens,
    resolve_llm_max_tool_result_tokens,
    resolve_llm_model,
    resolve_llm_reasoning_effort,
    resolve_max_tool_rounds,
)
from precursor.backend.services.events import publish_message_changed_chat
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.message_paging import list_message_window
from precursor.backend.services.note_drafts import (
    consume_note_draft_attachments_to_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats/{chat_id}/messages", tags=["chat"])


@router.get("", response_model=list[MessageRead], dependencies=[Depends(get_chat_or_404)])
async def list_messages(
    chat_id: int,
    limit: int | None = None,
    before_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    """List a chat's transcript, optionally as a cursor-paginated window.

    See the topic message router for the windowing semantics: no params returns
    the full transcript chronologically; ``limit`` returns the most recent
    ``limit`` rows; ``before_id`` pages further back. Slices come back oldest
    first.
    """
    return await list_message_window(
        session, Message.chat_id, chat_id, limit=limit, before_id=before_id
    )


@router.delete(
    "", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(get_chat_or_404)]
)
async def clear_messages(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Wipe the transcript for a chat. The chat row itself is kept."""
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

    # Bind any pre-uploaded attachments to this user message. We only adopt
    # rows that belong to the same chat and are still unbound, so a stale id
    # from another container / already-sent turn is silently dropped.
    bound_attachments: list[Attachment] = []
    if payload.attachment_ids:
        att_rows = await session.execute(
            select(Attachment).where(
                Attachment.id.in_(payload.attachment_ids),
                Attachment.chat_id == chat_id,
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
            kind="chat",
            container_id=chat_id,
            message_id=user_msg.id,
            attachment_ids=payload.note_attachment_ids,
        )
        if note_bound:
            await session.commit()
            bound_attachments.extend(note_bound)

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

    # When the chat opts into system-prompt mode, reassert the description as a
    # mandatory instruction on every user turn (no-op otherwise).
    history = _apply_chat_system_prompt(chat, history)

    enabled_servers = await _load_enabled_mcp_servers(session)
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
                "chat_id": a.chat_id,
                "message_id": a.message_id,
                "mime": a.mime,
                "size": a.size,
                "original_filename": a.original_filename,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in bound_attachments
        ],
    }

    inner = _run_message_stream(
        kind="chat",
        container_id=chat_id,
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
    return EventSourceResponse(_lifecycle_stream("chat", chat_id, inner))


@router.post("/notes/rephrase", response_model=NotesRephraseResponse)
async def notes_rephrase(
    chat_id: int,
    payload: NotesRephraseRequest,
    session: AsyncSession = Depends(get_session),
) -> NotesRephraseResponse:
    """Clean up rough notes via the LLM (no persistence)."""
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    user_prompt = notes_service.build_rephrase_user_prompt(
        container_label="Chat",
        title=chat.title,
        instruction=(payload.instruction or "").strip(),
        text=payload.text,
    )
    rebuilt = await _stream_llm(
        session, notes_service.REPHRASE_SYSTEM, user_prompt, label="/notes rephrase"
    )
    return NotesRephraseResponse(text=rebuilt or payload.text)


@router.post("/notes/append", response_model=NotesAppendResponse)
async def notes_append(
    chat_id: int,
    payload: NotesAppendRequest,
    session: AsyncSession = Depends(get_session),
) -> NotesAppendResponse:
    """Persist freeform notes verbatim as a user message in the chat."""
    if await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return await notes_service.append_notes(
        kind="chat",
        container_id=chat_id,
        text=payload.text,
        attachment_ids=payload.attachment_ids,
    )


@router.get(
    "/notes/draft",
    response_model=NotesDraftResponse,
    dependencies=[Depends(get_chat_or_404)],
)
async def notes_draft_get(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> NotesDraftResponse:
    return await notes_service.get_notes_draft(session, kind="chat", container_id=chat_id)


@router.put("/notes/draft", response_model=NotesDraftResponse)
async def notes_draft_save(
    chat_id: int,
    payload: NotesDraftSaveRequest,
    session: AsyncSession = Depends(get_session),
) -> NotesDraftResponse:
    if await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return await notes_service.save_notes_draft(
        session, kind="chat", container_id=chat_id, text=payload.text
    )


@router.delete(
    "/notes/draft",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_chat_or_404)],
)
async def notes_draft_delete(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    await notes_service.delete_notes_draft(session, kind="chat", container_id=chat_id)


@router.get(
    "/notes/attachments",
    response_model=list[NoteDraftAttachmentRead],
    dependencies=[Depends(get_chat_or_404)],
)
async def notes_attachments_list(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[NoteDraftAttachment]:
    return await notes_service.list_notes_attachments(session, kind="chat", container_id=chat_id)


@router.post(
    "/notes/attachments",
    response_model=NoteDraftAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def notes_attachments_upload(
    chat_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> NoteDraftAttachment:
    if await session.get(Chat, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return await notes_service.upload_notes_attachment(
        session, kind="chat", container_id=chat_id, file=file
    )


@router.delete(
    "/notes/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_chat_or_404)],
)
async def notes_attachments_delete(
    chat_id: int,
    attachment_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    await notes_service.delete_notes_attachment(
        session, kind="chat", container_id=chat_id, attachment_id=attachment_id
    )
