"""Derive a conversation title from what was actually said in it.

A fresh chat starts life with a placeholder title ("New chat"), which quickly
turns the sidebar into a wall of identical entries. Rather than make the user
name a conversation before having it, the server derives one from the opening
prompt with a short, tool-less LLM call and renames the chat in place.

Two entry points share the prompt and the sanitiser:

* :func:`schedule_autoname` — fire-and-forget, started by the chat stream
  endpoint right after the first user turn is persisted. It runs *alongside* the
  streaming answer, so the title normally lands while the reply is still being
  written rather than after it.
* :func:`suggest_title` — the ``/suggest-name`` command, which renames an
  existing topic or chat from its transcript on demand.

Naming is advisory by construction: a provider outage, a refusal, or output that
fails :func:`sanitize_title` leaves the existing title untouched. It never blocks
or fails the turn it rides along with.

Renaming is safe to do late because neither container re-slugs on a title
change: chats slug on a UUID (``routers/chats.py``) and topics only re-slug when
a slug is explicitly supplied (``routers/topics.py``). A link that is already
open somewhere keeps working.
"""

from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from precursor.backend.db import SessionLocal
from precursor.backend.models import Chat, Message, MessageRole, Topic
from precursor.backend.services.app_settings import (
    resolve_chat_autoname_enabled,
    resolve_chat_autoname_model,
)
from precursor.backend.services.events import publish_chat_changed, publish_topic_changed
from precursor.backend.services.llm import complete_text_with_usage, get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

#: Ledger source for the round-trip, alongside ``/refine`` and the other
#: utility calls.
USAGE_SOURCE = "/suggest-name"

#: Hard cap on the generated title. The column holds 255, but the sidebar
#: truncates long entries — which defeats the point of naming them.
MAX_TITLE_CHARS = 60

#: How much of the source text the model gets. A runaway paste must not blow up
#: the prompt budget for what is a throwaway naming call.
_MAX_SOURCE_CHARS = 2000

#: How many turns ``/suggest-name`` reads back when renaming an existing
#: conversation. The opening exchange carries almost all of the signal.
_TRANSCRIPT_TURNS = 10

_SYSTEM = (
    "You name conversations. Given the start of a conversation with an AI "
    "assistant, reply with a short title describing what it is about.\n"
    "Rules:\n"
    "- 3 to 6 words, never more than "
    f"{MAX_TITLE_CHARS} characters.\n"
    "- Write it in the same language the user wrote in.\n"
    "- Name the subject itself. Do not start with 'Chat about', "
    "'Conversation regarding', 'Help with', or similar filler.\n"
    "- Sentence case. No quotes, no surrounding punctuation, no trailing "
    "period, no Markdown, no emoji.\n"
    "- Reply with the title and nothing else — no preamble, no explanation."
)

# A leading label the model sometimes prepends despite being told not to.
_LABEL_RE = re.compile(
    r"^\s*(?:title|titre|název|título|titel)\s*[:：]\s*",  # noqa: RUF001 - CJK colon is intentional
    re.IGNORECASE,
)

# Paired wrappers a model may put around the title. Index-aligned: the opener at
# position *i* closes with the character at position *i* of ``_CLOSERS``.
_OPENERS = "\"'`“‘«<([{*_"  # noqa: RUF001 - typographic quotes are intentional
_CLOSERS = "\"'`”’»>)]}*_"  # noqa: RUF001 - typographic quotes are intentional

# Trailing sentence punctuation to shed, including the CJK forms a title written
# in Chinese or Japanese would end with. A closing "?" is deliberately kept:
# "Why is the build failing?" is a legitimate title, a trailing period is noise.
_TRAILING_PUNCT = ".,;:!。、，；：！ "  # noqa: RUF001 - CJK punctuation is intentional


def sanitize_title(raw: str) -> str:
    """Normalise a model-proposed title, or return ``""`` when unusable.

    Returning empty is the caller's signal to keep the existing title, so this
    rejects rather than salvages anything that doesn't look like a title: an
    empty reply, or prose long enough that the model clearly answered the
    conversation instead of naming it.
    """
    if not raw:
        return ""

    # Models occasionally add a line of commentary; the title is the first thing
    # they write, so keep that and drop the rest.
    title = next((line for line in raw.splitlines() if line.strip()), "")
    title = _LABEL_RE.sub("", title).strip()

    # Peel paired wrappers one layer at a time ("**\"Fix login\"**").
    while len(title) >= 2:
        idx = _OPENERS.find(title[0])
        if idx == -1 or title[-1] != _CLOSERS[idx]:
            break
        title = title[1:-1].strip()

    title = re.sub(r"\s+", " ", title).strip().strip(_TRAILING_PUNCT).strip()
    if not title:
        return ""

    # Well past a title and into an answer — the model misunderstood the task,
    # so keep whatever title the conversation already has.
    if len(title) > MAX_TITLE_CHARS * 3:
        return ""

    if len(title) > MAX_TITLE_CHARS:
        cut = title[:MAX_TITLE_CHARS]
        # Prefer a word boundary, but not one that leaves a stub behind.
        space = cut.rfind(" ")
        if space >= MAX_TITLE_CHARS // 2:
            cut = cut[:space]
        title = cut.strip().strip(_TRAILING_PUNCT).strip() + "…"

    return title


async def _generate(session: AsyncSession, source: str, *, topic_id: int | None = None) -> str:
    """Ask the model for a title for ``source``. Returns ``""`` on any failure."""
    text = source.strip()[:_MAX_SOURCE_CHARS]
    if not text:
        return ""

    model = await resolve_chat_autoname_model(session)
    try:
        provider = await get_llm_provider(session)
        raw, usage = await complete_text_with_usage(
            provider,
            model=model,
            messages=[
                ChatMessage(role="system", content=_SYSTEM),
                ChatMessage(role="user", content=text),
            ],
        )
    except Exception as exc:
        # Naming is a nicety riding along with a real turn — never surface this.
        logger.warning("Auto-name: LLM call failed: %s", exc)
        return ""

    if usage is not None:
        await record_usage(
            session,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            source=USAGE_SOURCE,
            model=model,
            topic_id=topic_id,
        )
        await session.commit()

    return sanitize_title(raw)


async def _transcript(
    session: AsyncSession,
    *,
    fk_column: InstrumentedAttribute[int | None],
    container_id: int,
) -> str:
    """Render the opening turns of a conversation as naming material.

    ``fk_column`` is the ``Message`` foreign key to the container
    (``Message.topic_id`` or ``Message.chat_id``).
    """
    result = await session.execute(
        select(Message)
        .where(fk_column == container_id)
        .where(Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)))
        .order_by(Message.created_at)
        .limit(_TRANSCRIPT_TURNS)
    )
    return "\n\n".join(f"[{m.role.value}] {m.content}" for m in result.scalars().all() if m.content)


# ---------------------------------------------------------------------------
# Automatic naming of a freshly-created chat.
# ---------------------------------------------------------------------------

# asyncio only holds a weak reference to a running task, so a fire-and-forget
# task can be garbage-collected mid-flight. Keep a strong reference until it
# finishes.
_pending: set[asyncio.Task[None]] = set()


def schedule_autoname(chat_id: int, *, prompt: str) -> None:
    """Kick off naming for ``chat_id`` without blocking the caller's turn."""
    task = asyncio.create_task(_autoname_chat(chat_id, prompt=prompt))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _autoname_chat(chat_id: int, *, prompt: str) -> None:
    """Name a chat from its first prompt, if it is still waiting for one.

    Runs detached from the request that started it, so it opens its own session
    (the request-scoped one is closed once the streaming response finishes) and
    re-checks the guard it was scheduled under — the user may have renamed the
    chat by hand in the meantime, and a manual title always wins.
    """
    try:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None or not chat.autoname_pending:
                return
            if not await resolve_chat_autoname_enabled(session):
                # Leave the flag set: turning the setting back on should still
                # name the conversations that were created while it was off.
                return

            title = await _generate(session, prompt)
            if not title:
                # Leave the flag set so a transient provider failure gets
                # another go on the next turn, rather than silently condemning
                # the chat to its placeholder for good.
                return

            # Re-read: the generation above is a network round-trip, and the user
            # may have typed their own title while it was in flight.
            await session.refresh(chat)
            if not chat.autoname_pending:
                return
            chat.title = title
            chat.autoname_pending = False
            await session.commit()

        # Detached from the request, but the contextvar carrying the originating
        # client id came along with the task — and every window drops events
        # tagged with its own id. Broadcast so the window that started the chat
        # isn't the one window that never sees the rename.
        await publish_chat_changed(chat_id, broadcast=True)
    except Exception as exc:
        logger.warning("Auto-name failed for chat %s: %s", chat_id, exc)


# ---------------------------------------------------------------------------
# /suggest-name — rename an existing conversation on demand.
# ---------------------------------------------------------------------------


async def suggest_chat_name(session: AsyncSession, chat: Chat) -> str:
    """Rename ``chat`` from its transcript. Returns the new title, or ``""``."""
    title = await _generate(
        session, await _transcript(session, fk_column=Message.chat_id, container_id=chat.id)
    )
    if not title:
        return ""
    chat.title = title
    # An explicit rename settles the name, so a queued auto-name must not
    # overwrite it later.
    chat.autoname_pending = False
    await session.commit()
    await publish_chat_changed(chat.id)
    return title


async def suggest_topic_name(session: AsyncSession, topic: Topic) -> str:
    """Rename ``topic`` from its transcript. Returns the new title, or ``""``."""
    source = await _transcript(session, fk_column=Message.topic_id, container_id=topic.id)
    title = await _generate(session, source, topic_id=topic.id)
    if not title:
        return ""
    topic.title = title
    await session.commit()
    await publish_topic_changed(topic.id)
    return title
