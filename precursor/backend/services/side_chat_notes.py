"""Send a side chat's outcome back to the topic it branched from.

The model condenses the side chat into a short note; the user reviews it, and
the note is filed into the parent topic as a user message (like ``/notes``),
so the topic's history and summary pick up what the tangent concluded.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import Chat, Message, MessageRole, Topic
from precursor.backend.schemas.message import MessageRead
from precursor.backend.services.app_settings import resolve_llm_max_input_tokens
from precursor.backend.services.compaction import chunk_blocks, render_transcript
from precursor.backend.services.conversation_turn import load_model_history
from precursor.backend.services.events import is_streaming, publish_message_changed
from precursor.backend.services.llm.one_shot import complete_once

USAGE_SOURCE = "/send-to-topic"
_CHARS_PER_TOKEN = 3
_CHUNK_BUDGET_RATIO = 0.6

_SYSTEM = """\
You condense a side discussion so it can be filed into the main topic it \
branched from. The reader knows the topic but not this side discussion.

Write a short Markdown note with these parts (skip one only if empty):
- One sentence on what was explored.
- **Conclusions** — decisions, answers and recommendations, as bullets.
- **Follow-ups** — open questions and next steps, as bullets.

Rules: copy names, numbers, commands and URLs verbatim; never invent \
anything; write in the conversation's language; no heading, preamble or \
closing remarks — output only the note."""


async def _linked_chat(session: AsyncSession, chat_id: int) -> tuple[Chat, Topic]:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    topic = await session.get(Topic, chat.parent_topic_id) if chat.parent_topic_id else None
    if topic is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This chat isn't linked to a topic any more.")
    return chat, topic


async def draft_topic_note(
    session: AsyncSession, chat_id: int, *, instructions: str | None = None
) -> tuple[str, Topic]:
    """Have the model write the note for the parent topic. Nothing is saved."""
    chat, topic = await _linked_chat(session, chat_id)
    if is_streaming("chat", chat_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A reply is still being generated; wait for it to finish."
        )
    history = await load_model_history(session, "chat", chat_id)
    if not any(m.role in ("user", "assistant") for m in history):
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to send yet.")

    budget_tokens = await resolve_llm_max_input_tokens(session)
    chunk_chars = int(budget_tokens * _CHUNK_BUDGET_RATIO) * _CHARS_PER_TOKEN
    focus = (instructions or "").strip()
    head = f"Main topic: {topic.title}"
    if topic.description:
        head += f"\nTopic description: {topic.description}"
    if chat.seed_content:
        head += (
            f"\n\nThe side discussion started from this reply in the topic:\n{chat.seed_content}"
        )

    note = ""
    chunks = chunk_blocks(render_transcript(history), chunk_chars)
    for idx, chunk in enumerate(chunks):
        parts = [head]
        if note:
            parts.append(f"Note so far (fold it into your new note):\n\n{note}")
        label = (
            "Side discussion"
            if len(chunks) == 1
            else f"Side discussion, part {idx + 1}/{len(chunks)}"
        )
        parts.append(f"{label}:\n\n{chunk}")
        if focus:
            parts.append(f"Extra focus requested by the user: {focus}")
        result = await complete_once(
            session,
            system=_SYSTEM,
            user="\n\n---\n\n".join(parts),
            usage_source=USAGE_SOURCE,
            chat_id=chat_id,
        )
        note = result.text.strip()
    if not note:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The model returned an empty note.")
    return note, topic


def topic_note_content(chat: Chat, text: str) -> str:
    """The note as filed into the topic, headed by a link back to the chat."""
    return f"**From side chat [{chat.title}](/chats/{chat.slug})**\n\n{text.strip()}"


async def send_topic_note(session: AsyncSession, chat_id: int, text: str) -> MessageRead:
    """File ``text`` into the parent topic as a user message, verbatim."""
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The note is empty.")
    chat, topic = await _linked_chat(session, chat_id)
    content = topic_note_content(chat, text)
    async with SessionLocal() as ws:
        msg = Message(topic_id=topic.id, role=MessageRole.USER, content=content)
        ws.add(msg)
        await ws.commit()
        await ws.refresh(msg, attribute_names=["attachments"])
        read = MessageRead.model_validate(msg, from_attributes=True)
    await publish_message_changed(topic.id)
    return read
