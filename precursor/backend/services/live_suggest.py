"""Suggested answers for a live meeting — the fast path.

When another participant asks a question, the Live view calls this to stream a
short answer the user can say out loud. It is one streaming call on the fast
model (no tools, small context) that either answers or emits ``NONE_MARKER``
when the utterance isn't a question that needs the user's answer, so there is no
separate classification round trip. The exchange is then persisted into the
session's chat as an unattended turn (a ``live_question`` row followed by a
``live_suggestion`` row) so the user can follow up there.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.models import MeetingInsight, MeetingSegment, MeetingSession, Message
from precursor.backend.models.message import MessageRole
from precursor.backend.services.app_settings import (
    resolve_live_fast_model,
    resolve_live_reasoning_effort,
    resolve_llm_model,
)
from precursor.backend.services.events import (
    publish_message_changed_chat,
)
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage, TextDeltaEvent, UsageEvent
from precursor.backend.services.meeting_analysis import (
    _format_transcript,
    _topic_context,
    context_notes_text,
    display_label,
    language_name,
    meeting_context_text,
    session_persona,
)
from precursor.backend.services.model_fallbacks import resolve_model_category
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

NONE_MARKER = "[[NONE]]"
KIND_QUESTION = "live_question"
KIND_SUGGESTION = "live_suggestion"

# Small on purpose: the question is what matters, and every extra character is
# latency. The insights snapshot already carries the older context.
_TRANSCRIPT_TAIL_CHARS = 1800
_TOPIC_CHARS = 1500
_MAX_INSIGHTS = 8

_SYSTEM_PROMPT = (
    "You are a live meeting coach whispering in the user's ear. Someone else in "
    "the meeting just asked a question. Write what the user could say in reply: "
    "direct, accurate, 1-4 short sentences, in the first person, ready to read "
    "aloud. Use the meeting context below and your own knowledge. If you are "
    "unsure of a fact, say so briefly rather than inventing it. No preamble, no "
    "headings.\n\n"
    f"If the last utterance is not a question or request that expects an answer "
    f"from the user (small talk, a rhetorical question, a question the user "
    f"already answered, one clearly aimed at someone else), output exactly "
    f"{NONE_MARKER} and nothing else."
)


@dataclass
class SuggestionPlan:
    messages: list[ChatMessage]
    provider: object
    model: str
    effort: str | None
    topic_id: int | None
    chat_id: int
    speaker: str


async def prepare_suggestion(
    session: AsyncSession,
    ms: MeetingSession,
    question: str,
    speaker_label: str | None,
    chat_id: int,
) -> SuggestionPlan:
    segments = list(
        (
            await session.execute(
                select(MeetingSegment)
                .where(MeetingSegment.session_id == ms.id)
                .order_by(MeetingSegment.created_at.desc(), MeetingSegment.id.desc())
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    segments.reverse()
    insights = list(
        (
            await session.execute(
                select(MeetingInsight)
                .where(MeetingInsight.session_id == ms.id)
                .order_by(MeetingInsight.created_at.desc(), MeetingInsight.id.desc())
                .limit(_MAX_INSIGHTS)
            )
        )
        .scalars()
        .all()
    )

    system = _SYSTEM_PROMPT
    lang = language_name(ms.language)
    if lang:
        system += f" Respond in {lang}."
    persona = await session_persona(session, ms.role)
    if persona:
        system += f"\n\n{persona}"

    # Stable context first and the question last, so a provider's prompt cache
    # can reuse the prefix between consecutive questions.
    parts: list[str] = []
    meeting_ctx = meeting_context_text(ms.external_meeting)
    if meeting_ctx:
        parts.append(f"Linked meeting:\n{meeting_ctx}")
    topic_ctx = (await _topic_context(session, ms.topic_id))[-_TOPIC_CHARS:]
    if topic_ctx:
        parts.append(f"Attached topic:\n{topic_ctx}")
    notes_ctx = context_notes_text(ms.context_notes)
    if notes_ctx:
        parts.append(f"Pinned context notes:\n{notes_ctx}")
    if insights:
        parts.append(
            "Live insights:\n" + "\n".join(f"- [{i.kind}] {i.content}" for i in reversed(insights))
        )
    names = ms.speaker_names
    tail = _format_transcript(segments, names)[-_TRANSCRIPT_TAIL_CHARS:]
    if tail:
        parts.append(f"Recent transcript:\n{tail}")
    speaker = display_label(speaker_label, names)
    parts.append(f"Question asked by {speaker}:\n{question}")

    model = await resolve_live_fast_model(session, use_default=False)
    category = await resolve_model_category(session) if not model else None
    provider = (
        await get_llm_provider(session, model_category=category)
        if category is not None
        else await get_llm_provider(session)
    )
    model = model or await resolve_llm_model(session)
    return SuggestionPlan(
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="\n\n".join(parts)),
        ],
        provider=provider,
        model=model,
        effort=await resolve_live_reasoning_effort(session) or None,
        topic_id=ms.topic_id,
        chat_id=chat_id,
        speaker=speaker,
    )


def _sse(event: str, data: dict[str, object]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data)}


async def stream_suggestion(plan: SuggestionPlan, question: str) -> AsyncIterator[dict[str, str]]:
    """Yield SSE frames: ``token``*, then ``none`` or ``done`` (or ``error``).

    A client that disconnects cancels the generator; nothing is persisted then.
    """
    started = time.monotonic()
    held = ""
    decided = False
    text = ""
    usage: UsageEvent | None = None
    try:
        provider = plan.provider
        async for event in provider.stream_chat_with_tools(  # type: ignore[attr-defined]
            model=getattr(provider, "effective_model", None) or plan.model,
            messages=plan.messages,
            tools=[],
            reasoning_effort=plan.effort,
        ):
            if isinstance(event, UsageEvent):
                usage = event
                continue
            if not isinstance(event, TextDeltaEvent) or not event.content:
                continue
            if not decided:
                # Hold the opening characters until we know whether this is
                # the NONE marker, so it never flashes in the UI.
                held += event.content
                probe = held.lstrip()
                if len(probe) < len(NONE_MARKER) and NONE_MARKER.startswith(probe):
                    continue
                decided = True
                if probe.startswith(NONE_MARKER):
                    yield _sse("none", {})
                    return
                text = held
                yield _sse("token", {"content": held})
                continue
            text += event.content
            yield _sse("token", {"content": event.content})

        answer = text.strip()
        if not answer or answer.startswith(NONE_MARKER):
            yield _sse("none", {})
            return
        message_id = await _persist(plan, question, answer, usage, started)
        yield _sse("done", {"message_id": message_id})
    except Exception as exc:
        logger.warning("Live suggestion failed: %s", exc)
        yield _sse("error", {"message": str(exc)})


async def _persist(
    plan: SuggestionPlan,
    question: str,
    answer: str,
    usage: UsageEvent | None,
    started: float,
) -> int:
    # A fresh session: the request-scoped one may be closed by now.
    async with SessionLocal() as ws:
        ws.add(
            Message(
                role=MessageRole.USER,
                content=f"[{plan.speaker}] {question}",
                kind=KIND_QUESTION,
                chat_id=plan.chat_id,
            )
        )
        reply = Message(
            role=MessageRole.ASSISTANT,
            content=answer,
            kind=KIND_SUGGESTION,
            chat_id=plan.chat_id,
            model=plan.model,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )
        ws.add(reply)
        if usage is not None:
            await record_usage(
                ws,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                source="/live-suggest",
                model=plan.model,
                topic_id=plan.topic_id,
            )
        await ws.commit()
        await ws.refresh(reply)
        message_id = reply.id
    await publish_message_changed_chat(plan.chat_id)
    return message_id
