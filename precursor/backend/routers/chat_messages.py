"""Chat message endpoints — list, delete, and stream turns for flat chat sessions.

Mirrors the topic chat router but targets ``Chat`` containers (no GitHub issue
context). Turn preparation and streaming are shared with it through
:mod:`precursor.backend.services.conversation_turn`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import get_session
from precursor.backend.models import (
    Chat,
    Message,
    MessageRole,
    NoteDraftAttachment,
)
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
    SuggestNameResponse,
)
from precursor.backend.services import notes as notes_service
from precursor.backend.services import skills as skills_service
from precursor.backend.services.chat_autoname import schedule_autoname, suggest_chat_name
from precursor.backend.services.conversation_turn import (
    persist_user_turn,
    resolve_turn_settings,
    snapshot_history,
    stream_turn,
    user_echo,
)
from precursor.backend.services.events import publish_message_changed_chat
from precursor.backend.services.llm.one_shot import complete_once
from precursor.backend.services.message_paging import list_message_window
from precursor.backend.services.turn_engine import (
    apply_chat_system_prompt,
    build_chat_system_context,
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


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(get_chat_or_404)])
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

    user_msg, attachments = await persist_user_turn(session, "chat", chat_id, payload)

    # A chat created with a placeholder title gets a real one derived from this
    # prompt. Fired as a detached task rather than awaited: naming is a side
    # errand, and the answer must not wait on it. It typically lands while the
    # reply is still streaming. Retries are included — the flag stays set until a
    # name actually sticks, and a retried turn is often the one that succeeds.
    # `prompt_override` is preferred so a skill invocation names the chat after
    # what it actually asked rather than after `/to-en …`.
    if chat.autoname_pending:
        schedule_autoname(chat_id, prompt=payload.prompt_override or user_msg.content)

    # Snapshot history + system context now, before the session closes.
    system_prompt = await build_chat_system_context(session, chat)
    history = await snapshot_history(
        session, "chat", chat_id, prompt_override=payload.prompt_override
    )

    # When the chat opts into system-prompt mode, reassert the description as a
    # mandatory instruction on every user turn (no-op otherwise). The description
    # may reference skills (``/rewrite``), expanded here while the session is open.
    history = apply_chat_system_prompt(
        chat,
        history,
        description=await skills_service.expand_references(session, chat.description or ""),
    )

    settings = await resolve_turn_settings(session, model_override=payload.model)
    return EventSourceResponse(
        stream_turn(
            "chat",
            chat_id,
            system_prompt=system_prompt,
            history=history,
            echo=user_echo("chat", user_msg, attachments),
            settings=settings,
        )
    )


@router.post("/suggest-name", response_model=SuggestNameResponse)
async def suggest_name(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
) -> SuggestNameResponse:
    """Rename this chat from its transcript (the ``/suggest-name`` command)."""
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return SuggestNameResponse(title=await suggest_chat_name(session, chat))


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
    result = await complete_once(
        session,
        system=notes_service.REPHRASE_SYSTEM,
        user=user_prompt,
        usage_source="/notes rephrase",
        chat_id=chat_id,
    )
    return NotesRephraseResponse(text=result.text or payload.text)


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
