"""Live meeting summary — generate a markdown recap from the transcript.

On demand (or as a draft when a session ends), summarise the full transcript
plus the derived insights and any attached topic context into a concise markdown
recap. Written in the session's language. The recap can then be appended as a
message into the attached topic thread.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import MeetingInsight, MeetingSegment, MeetingSession, Topic
from precursor.backend.services.app_settings import resolve_llm_model
from precursor.backend.services.llm import complete_text_with_usage, get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.meeting_analysis import (
    context_notes_text,
    display_label,
    language_name,
    meeting_context_text,
)
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

_TRANSCRIPT_CHARS = 16000
_TOPIC_CHARS = 4000

_KIND_LABELS = {
    "action_item": "Action item",
    "decision": "Decision",
    "question": "Open question",
    "suggestion": "Suggestion",
    "risk": "Risk",
    "note": "Note",
}

_SYSTEM_PROMPT = (
    "You are a meeting assistant. Write a concise, well-structured markdown "
    "summary of the meeting from the transcript, the derived insights, and any "
    "attached topic context. Use these sections, omitting any that have no "
    "content:\n"
    "## Summary — 2-4 sentences of what the meeting was about and its outcome.\n"
    "## Attendees — bullet list of who took part (only if attendees are given).\n"
    "## Decisions — bullet list.\n"
    "## Action items — bullet list, include an owner when one was named.\n"
    "## Open questions — bullet list.\n"
    "## Risks — bullet list.\n"
    "Be faithful to what was said; do not invent owners or decisions. No "
    "preamble, no closing remarks — just the markdown."
)


def _format_transcript(segments: list[MeetingSegment], names: dict[str, str]) -> str:
    lines = [f"[{display_label(s.speaker_label, names)}] {s.text}" for s in segments]
    return "\n".join(lines)[-_TRANSCRIPT_CHARS:]


def _format_insights(insights: list[MeetingInsight]) -> str:
    if not insights:
        return ""
    return "\n".join(f"- ({_KIND_LABELS.get(i.kind, i.kind)}) {i.content}" for i in insights)


async def _topic_context(session: AsyncSession, topic_id: int | None) -> str:
    if topic_id is None:
        return ""
    topic = await session.get(Topic, topic_id)
    if topic is None:
        return ""
    parts = [f"Topic: {topic.title}"]
    if topic.description:
        parts.append(topic.description)
    return "\n".join(parts)[-_TOPIC_CHARS:]


async def generate_summary(session: AsyncSession, session_id: int) -> tuple[str, str]:
    """Generate a markdown summary for a session. Returns ``(text, model)``."""
    ms = await session.get(MeetingSession, session_id)
    if ms is None:
        return "", ""

    segments = list(
        (
            await session.execute(
                select(MeetingSegment)
                .where(MeetingSegment.session_id == session_id)
                .order_by(MeetingSegment.created_at, MeetingSegment.id)
            )
        )
        .scalars()
        .all()
    )
    if not segments:
        return "", ""

    insights = list(
        (
            await session.execute(
                select(MeetingInsight)
                .where(MeetingInsight.session_id == session_id)
                .order_by(MeetingInsight.created_at, MeetingInsight.id)
            )
        )
        .scalars()
        .all()
    )

    system = _SYSTEM_PROMPT
    lang = language_name(ms.language)
    if lang:
        system += f"\n\nWrite the entire summary in {lang}."

    user_parts = [
        f"Meeting title: {ms.title}",
    ]
    if ms.attendees:
        user_parts.append("Attendees: " + ", ".join(ms.attendees))
    user_parts.append(f"\nTranscript:\n{_format_transcript(segments, ms.speaker_names)}")
    insight_block = _format_insights(insights)
    if insight_block:
        user_parts.append(f"\nDerived insights:\n{insight_block}")
    meeting_ctx = meeting_context_text(ms.external_meeting)
    if meeting_ctx:
        user_parts.append(f"\nLinked meeting context:\n{meeting_ctx}")
    topic_ctx = await _topic_context(session, ms.topic_id)
    if topic_ctx:
        user_parts.append(f"\nAttached topic context:\n{topic_ctx}")
    notes_ctx = context_notes_text(ms.context_notes)
    if notes_ctx:
        user_parts.append(f"\nPinned context notes:\n{notes_ctx}")

    provider = await get_llm_provider(session)
    # Use the default chat model for a higher-quality recap (not the fast model).
    model = await resolve_llm_model(session)
    text, usage = await complete_text_with_usage(
        provider,
        model=model,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="\n".join(user_parts)),
        ],
    )
    if usage is not None:
        await record_usage(
            session,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            source="/live-summary",
            model=model,
            topic_id=ms.topic_id,
        )
        await session.commit()
    return text, model


_TOPIC_SUMMARY_SYSTEM = (
    "You are briefing someone who is about to join a meeting about this topic. "
    "From the topic's conversation (and any linked GitHub issue summary), write a "
    "short markdown context brief: 4-8 bullets covering what it's about, the "
    "current state, key decisions so far, open questions, and likely next actions. "
    "Be faithful; no preamble."
)


async def summarize_topic_conversation(
    session: AsyncSession, topic_id: int, *, language: str | None = None
) -> tuple[str, str]:
    """Summarize a topic's conversation as meeting context. Returns (text, model)."""
    from precursor.backend.models import IssueContextCache, Message

    topic = await session.get(Topic, topic_id)
    if topic is None:
        return "", ""

    rows = list(
        (
            await session.execute(
                select(Message)
                .where(Message.topic_id == topic_id)
                .order_by(Message.created_at.desc())
                .limit(60)
            )
        )
        .scalars()
        .all()
    )

    parts: list[str] = [f"Topic: {topic.title}"]
    if topic.description:
        parts.append(f"Description: {topic.description}")
    cache = await session.get(IssueContextCache, topic_id)
    if cache is not None and cache.summary:
        parts.append(f"Linked issue summary:\n{cache.summary}")
    convo = "\n".join(f"{m.role.value}: {m.content}" for m in reversed(rows) if m.content)
    if convo:
        parts.append(f"Conversation:\n{convo}")
    if len(parts) <= 1 and cache is None:
        return "", ""

    system = _TOPIC_SUMMARY_SYSTEM
    lang = language_name(language)
    if lang:
        system += f"\n\nWrite the brief in {lang}."

    provider = await get_llm_provider(session)
    model = await resolve_llm_model(session)
    text, usage = await complete_text_with_usage(
        provider,
        model=model,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="\n\n".join(parts)[-16000:]),
        ],
    )
    if usage is not None:
        await record_usage(
            session,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            source="/live-topic-context",
            model=model,
            topic_id=topic_id,
        )
        await session.commit()
    return text, model
