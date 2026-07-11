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
from precursor.backend.services.meeting_analysis import display_label, language_name
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
        f"\nTranscript:\n{_format_transcript(segments, ms.speaker_names)}",
    ]
    insight_block = _format_insights(insights)
    if insight_block:
        user_parts.append(f"\nDerived insights:\n{insight_block}")
    topic_ctx = await _topic_context(session, ms.topic_id)
    if topic_ctx:
        user_parts.append(f"\nAttached topic context:\n{topic_ctx}")

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
