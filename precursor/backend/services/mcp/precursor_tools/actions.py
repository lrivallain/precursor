"""Write tools that act on a topic: append a note, start a side chat, post a message."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote as quote_url

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    Message,
    MessageRole,
    Topic,
)
from precursor.backend.services.app_settings import (
    resolve_scheduled_run_timeout_seconds,
)
from precursor.backend.services.links import app_base_url

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import (
    _GATED,
    _chat_dict,
    _section_enabled,
    _topic_ref,
    mcp,
)


# --------------------------------------------------------------------------
# notes (write)
# --------------------------------------------------------------------------
@mcp.tool()
async def append_note(topic_id: int, text: str) -> dict[str, Any]:
    """Append a note to a topic verbatim, without running an assistant turn.

    The counterpart to ``post_message``: this persists ``text`` as-is and
    returns immediately, so it is the right tool for filing an already-written
    briefing, digest or summary into a topic. ``post_message`` would instead
    spend a full generation replying to it.

    Identical in effect to the app's "append notes" command, so the note lands
    in the topic exactly as the UI would write it.

    Unless the user explicitly asks for a separate note, do not use this tool
    to copy an intermediate artifact or publish a second copy of a delegated
    step plan's output. A running plan has not finished just because all its
    steps are validated; the app handles its final output and hand-back
    according to the source conversation's autonomy.

    The result echoes the destination under ``topic`` (``title``, slug ``path``
    and in-app ``url``). Report *those* back to the user rather than the numeric
    id, which is an internal detail they never see and cannot check.
    """
    if not await _section_enabled("notes"):
        return {"error": _GATED.format(section="notes")}
    if not text.strip():
        return {"error": "text must not be empty"}
    # Imported lazily: drags in the FastAPI/attachment stack the read tools
    # have no use for.
    from precursor.backend.services import notes as notes_service

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return {"error": f"Topic {topic_id} not found"}
        ref = await _topic_ref(session, topic)

    result = await notes_service.append_notes(
        kind="topic",
        container_id=topic_id,
        text=text,
        attachment_ids=[],
    )
    return {
        "topic_id": topic_id,
        "topic": ref,
        "message_id": result.message.id,
        "posted": True,
    }


# --------------------------------------------------------------------------
# side chats (write)
# --------------------------------------------------------------------------
@mcp.tool()
async def create_side_chat(
    topic_id: int, message_id: int | None = None, quote: str | None = None
) -> dict[str, Any]:
    """Start a side chat: a chat linked to a topic, for a tangent off it.

    The chat's model sees the topic's title, description and summary on every
    turn. Pass ``message_id`` (an assistant reply of that topic, from
    ``list_messages``) to start from that reply: the chat keeps a copy of it.
    ``quote`` narrows that copy to an excerpt of the reply.

    Nothing is generated: the chat is created empty, ready for the user. The
    result carries the chat's in-app ``url`` and its ``topic``; report those
    rather than numeric ids.
    """
    if not await _section_enabled("side_chats"):
        return {"error": _GATED.format(section="side_chats")}
    # Imported lazily: drags in the FastAPI stack the read tools don't need.
    from fastapi import HTTPException

    from precursor.backend.services import side_chats
    from precursor.backend.services.events import publish_topic_changed

    async with SessionLocal() as session:
        try:
            chat = await side_chats.create_side_chat(
                session, topic_id, message_id=message_id, quote=quote
            )
        except HTTPException as exc:
            return {"error": str(exc.detail)}
        topic = await session.get(Topic, topic_id)
        assert topic is not None
        ref = await _topic_ref(session, topic)
    await publish_topic_changed(topic_id)
    return {
        **_chat_dict(chat),
        "url": f"{app_base_url()}/chats/{quote_url(chat.slug)}",
        "topic": ref,
    }


# --------------------------------------------------------------------------
# post_message (write)
# --------------------------------------------------------------------------
@mcp.tool()
async def post_message(topic_id: int, content: str) -> dict[str, Any]:
    """Post a user message to a topic and run a full assistant turn; return the reply.

    This drives a complete generation (system context, history, tool loop) just
    like the chat UI, then returns the assistant's response. The nested turn
    does NOT re-expose Precursor's own MCP tools, so it can't recursively call
    ``post_message``.

    The result echoes the destination under ``topic`` (``title``, slug ``path``
    and in-app ``url``). Report *those* back to the user rather than the numeric
    id, which is an internal detail they never see and cannot check.
    """
    if not await _section_enabled("post_message"):
        return {"error": _GATED.format(section="post_message")}
    if not content.strip():
        return {"error": "content must not be empty"}
    # Imported lazily: pulls in the LLM/MCP stack we don't need for read tools.
    from precursor.backend.services.turn import run_topic_turn_with_timeout

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return {"error": f"Topic {topic_id} not found"}
        ref = await _topic_ref(session, topic)
        timeout = await resolve_scheduled_run_timeout_seconds(session)

    try:
        await run_topic_turn_with_timeout(topic_id, content, timeout=float(timeout))
    except TimeoutError:
        return {"error": f"Assistant turn timed out after {timeout}s"}

    async with SessionLocal() as session:
        latest = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.topic_id == topic_id)
                    .where(Message.role == MessageRole.ASSISTANT)
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    if latest is None:
        return {
            "topic_id": topic_id,
            "topic": ref,
            "reply": None,
            "note": "No assistant reply produced",
        }
    return {"topic_id": topic_id, "topic": ref, "reply": latest.content, "message_id": latest.id}
