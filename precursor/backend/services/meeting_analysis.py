"""Live meeting analysis — derive insights from the rolling transcript window.

Runs a fast, low-effort LLM pass over the recent transcript (plus the attached
topic's context, when set) and replaces the session's insight snapshot with the
current understanding: action items, decisions, open questions, suggested
answers, risks. Latency is prioritised over depth — this is meant to run every
~15-30s while a meeting is live.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import MeetingInsight, MeetingSegment, MeetingSession, Message, Topic
from precursor.backend.services.app_settings import (
    resolve_live_fast_model,
    resolve_live_reasoning_effort,
)
from precursor.backend.services.llm import complete_text_with_usage, get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

# Characters of transcript / topic context fed to the model. Kept modest so the
# analysis stays fast; the snapshot is re-derived each run so older context that
# has fallen out of the window is still reflected in prior insights.
_TRANSCRIPT_CHARS = 6000
_TOPIC_CHARS = 4000

VALID_KINDS = {"action_item", "decision", "question", "suggestion", "risk", "note"}

_SYSTEM_PROMPT = (
    "You are a live meeting assistant. From the running transcript (and any "
    "attached topic context), extract the CURRENT state of the discussion as a "
    "concise, de-duplicated set of insights. Prioritise being fast and useful "
    "over exhaustive.\n\n"
    "Return ONLY a JSON object of this exact shape, with no prose or code fences:\n"
    '{"insights": [{"kind": "<kind>", "content": "<one short sentence>"}]}\n\n'
    "Allowed kinds: action_item, decision, question, suggestion, risk, note.\n"
    "- action_item: a concrete task, ideally with an owner if stated.\n"
    "- decision: something the group agreed or concluded.\n"
    "- question: an open question raised but not resolved.\n"
    "- suggestion: a proposed answer or solution to a problem being discussed.\n"
    "- risk: a blocker, concern, or risk.\n"
    "- note: any other salient point.\n"
    "Keep each content under ~140 characters. Return at most ~12 insights. "
    "If nothing substantive has been said yet, return an empty list."
)


def _format_transcript(segments: list[MeetingSegment]) -> str:
    lines: list[str] = []
    for seg in segments:
        speaker = seg.speaker_label or "Speaker"
        lines.append(f"[{speaker}] {seg.text}")
    text = "\n".join(lines)
    # Keep the most recent window when the transcript is long.
    return text[-_TRANSCRIPT_CHARS:]


async def _topic_context(session: AsyncSession, topic_id: int | None) -> str:
    if topic_id is None:
        return ""
    topic = await session.get(Topic, topic_id)
    if topic is None:
        return ""
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.topic_id == topic_id)
                .order_by(Message.created_at.desc())
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    parts: list[str] = [f"Topic: {topic.title}"]
    if topic.description:
        parts.append(topic.description)
    # rows are newest-first; render oldest-first for readability.
    for msg in reversed(rows):
        if msg.content:
            parts.append(f"{msg.role.value}: {msg.content}")
    return "\n".join(parts)[-_TOPIC_CHARS:]


def _parse_insights(text: str) -> list[tuple[str, str]]:
    """Best-effort parse of the model's JSON into (kind, content) pairs."""
    raw = text.strip()
    # Tolerate accidental code fences.
    if raw.startswith("```"):
        raw = raw.strip("`")
        nl = raw.find("\n")
        if nl != -1:
            raw = raw[nl + 1 :]
    # Isolate the outermost object if the model added stray prose.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Meeting analysis returned unparseable JSON")
        return []
    items = data.get("insights") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if kind in VALID_KINDS and content:
            out.append((kind, content[:500]))
    return out[:12]


async def analyze_session(session: AsyncSession, session_id: int) -> list[MeetingInsight]:
    """Re-derive and persist the insight snapshot for a session.

    Replaces the session's existing insights with the fresh set. Returns the new
    rows (ordered), or the existing set unchanged when there's no transcript yet.
    """
    ms = await session.get(MeetingSession, session_id)
    if ms is None:
        return []

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
        return []

    transcript = _format_transcript(segments)
    topic_ctx = await _topic_context(session, ms.topic_id)

    user_parts = [f"Transcript so far:\n{transcript}"]
    if topic_ctx:
        user_parts.append(f"\nAttached topic context:\n{topic_ctx}")

    provider = await get_llm_provider(session)
    model = await resolve_live_fast_model(session)
    effort = await resolve_live_reasoning_effort(session)
    try:
        text, usage = await complete_text_with_usage(
            provider,
            model=model,
            messages=[
                ChatMessage(role="system", content=_SYSTEM_PROMPT),
                ChatMessage(role="user", content="\n".join(user_parts)),
            ],
            reasoning_effort=effort or None,
        )
    except Exception as exc:  # keep the meeting alive even if analysis fails
        logger.warning("Meeting analysis LLM call failed: %s", exc)
        raise

    insights = _parse_insights(text)

    # Replace the snapshot so the panel always reflects the latest understanding.
    await session.execute(delete(MeetingInsight).where(MeetingInsight.session_id == session_id))
    rows = [
        MeetingInsight(session_id=session_id, kind=kind, content=content)
        for kind, content in insights
    ]
    session.add_all(rows)
    if usage is not None:
        await record_usage(
            session,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            source="/live-analysis",
            model=model,
        )
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows
