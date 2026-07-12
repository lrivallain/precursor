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
import re
from html.parser import HTMLParser
from typing import ClassVar

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

# BCP-47 tag -> human language name for prompting. Falls back to the raw tag.
_LANG_NAMES = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "ro": "Romanian",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "tr": "Turkish",
}


def language_name(tag: str | None) -> str | None:
    """Map a BCP-47 tag (e.g. ``fr-FR``) to a language name, or None when unset."""
    if not tag:
        return None
    base = tag.split("-")[0].lower()
    return _LANG_NAMES.get(base, tag)


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


# Diarization labels are scoped to a recording run as ``"<run>:<label>"`` (e.g.
# "2:Guest-1") because Azure re-numbers speakers on every stop/restart. The run
# prefix keeps a rename ("2:Guest-1" -> "Marie") from bleeding onto a different
# voice that happens to reuse the same raw label in another run. It's stripped
# for display when no explicit name is set.
_RUN_PREFIX = re.compile(r"^\d+:")


def strip_run_prefix(label: str) -> str:
    """Drop the ``<run>:`` recording-run prefix from a diarization label."""
    return _RUN_PREFIX.sub("", label)


def display_label(raw: str | None, names: dict[str, str]) -> str:
    """Resolve a raw diarization label to its display name (fallback: raw)."""
    if not raw:
        return "Speaker"
    if raw in names:
        return names[raw]
    return strip_run_prefix(raw)


def meeting_context_text(external_meeting: dict[str, object] | None) -> str:
    """Compact text describing a linked calendar meeting, for grounding prompts."""
    if not external_meeting:
        return ""
    m = external_meeting
    parts: list[str] = []
    subject = m.get("subject")
    if subject:
        parts.append(f"Meeting: {subject}")
    organizer = m.get("organizer")
    if organizer:
        parts.append(f"Organizer: {organizer}")
    attendees = m.get("attendees")
    if isinstance(attendees, list) and attendees:
        names = [str(a.get("name")) for a in attendees if isinstance(a, dict) and a.get("name")]
        if names:
            parts.append("Invitees: " + ", ".join(names[:40]))
    preview = m.get("body_preview")
    if isinstance(preview, str) and preview.strip():
        parts.append("Meeting notes:\n" + preview.strip()[:2000])
    return "\n".join(parts)


def context_notes_text(notes: list[str] | None) -> str:
    """Render user-pinned context notes as a compact bulleted block for prompts."""
    if not notes:
        return ""
    lines = [f"- {n.strip()}" for n in notes if n and n.strip()]
    return "\n".join(lines)[:4000]


async def live_chat_grounding(session: AsyncSession, chat_id: int) -> str:
    """Grounding block for a chat attached to a live meeting session.

    Returns the current meeting context — transcript tail, live insights, the
    user's notes, pinned context notes, the linked meeting, and the attached
    topic — rebuilt each turn so the chat always sees the latest state. Empty
    when the chat isn't attached to a meeting session.
    """
    ms = (
        (await session.execute(select(MeetingSession).where(MeetingSession.chat_id == chat_id)))
        .scalars()
        .first()
    )
    if ms is None:
        return ""

    segments = list(
        (
            await session.execute(
                select(MeetingSegment)
                .where(MeetingSegment.session_id == ms.id)
                .order_by(MeetingSegment.created_at, MeetingSegment.id)
            )
        )
        .scalars()
        .all()
    )
    insights = list(
        (
            await session.execute(
                select(MeetingInsight)
                .where(MeetingInsight.session_id == ms.id)
                .order_by(MeetingInsight.created_at, MeetingInsight.id)
            )
        )
        .scalars()
        .all()
    )

    parts: list[str] = [
        "This chat assists during a LIVE meeting. Ground your answers in the "
        "meeting context below; it updates as the meeting progresses. Prefer it "
        "over prior assumptions for anything time-sensitive."
    ]
    if segments:
        parts.append(f"Transcript so far:\n{_format_transcript(segments, ms.speaker_names)}")
    if insights:
        parts.append("Live insights:\n" + "\n".join(f"- [{i.kind}] {i.content}" for i in insights))
    if ms.notes.strip():
        parts.append(f"The user's live notes:\n{ms.notes.strip()[:4000]}")
    notes_ctx = context_notes_text(ms.context_notes)
    if notes_ctx:
        parts.append(f"Pinned context notes:\n{notes_ctx}")
    meeting_ctx = meeting_context_text(ms.external_meeting)
    if meeting_ctx:
        parts.append(f"Linked meeting:\n{meeting_ctx}")
    if ms.topic_id is not None:
        topic = await session.get(Topic, ms.topic_id)
        if topic is not None:
            line = f"Attached topic: {topic.title}"
            if topic.description:
                line += f" — {topic.description}"
            parts.append(line)
    lang = language_name(ms.language)
    if lang:
        parts.append(f"Reply in {lang} unless the user writes in another language.")
    return "\n\n".join(parts)


class _HTMLTextExtractor(HTMLParser):
    """Collapse HTML into readable plain text (block tags become line breaks)."""

    _BLOCK: ClassVar[set[str]] = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6"}
    _DROP: ClassVar[set[str]] = {"script", "style", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._DROP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self.parts.append(data)


def html_to_text(raw_html: str) -> str:
    """Best-effort HTML → plain text, collapsing runs of blank lines."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        return ""
    text = "".join(parser.parts).replace("\xa0", " ")
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def meeting_details_markdown(external_meeting: dict[str, object] | None) -> str:
    """Render a linked meeting's details as a Markdown block for a topic post."""
    if not external_meeting:
        return ""
    m = external_meeting
    lines: list[str] = [f"**Meeting — {m.get('subject') or '(no subject)'}**", ""]

    start, end = m.get("start"), m.get("end")
    when = " to ".join(str(x) for x in (start, end) if x)
    if when:
        lines.append(f"- **When:** {when}")
    organizer = m.get("organizer")
    if organizer:
        lines.append(f"- **Organizer:** {organizer}")
    attendees = m.get("attendees")
    if isinstance(attendees, list) and attendees:
        names = [str(a.get("name")) for a in attendees if isinstance(a, dict) and a.get("name")]
        if names:
            lines.append(f"- **Invitees:** {', '.join(names[:60])}")

    # Prefer the full body (HTML) over Graph's ~255-char bodyPreview so the post
    # isn't cut mid-sentence; fall back to the preview when there's no body.
    body_html = m.get("body")
    preview = m.get("body_preview")
    body_text = ""
    if isinstance(body_html, str) and body_html.strip():
        body_text = html_to_text(body_html)
    if not body_text and isinstance(preview, str):
        body_text = preview.strip()
    if body_text:
        lines += ["", body_text[:8000]]
    return "\n".join(lines)


def _format_transcript(segments: list[MeetingSegment], names: dict[str, str]) -> str:
    lines: list[str] = []
    for seg in segments:
        lines.append(f"[{display_label(seg.speaker_label, names)}] {seg.text}")
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

    transcript = _format_transcript(segments, ms.speaker_names)
    topic_ctx = await _topic_context(session, ms.topic_id)

    system = _SYSTEM_PROMPT
    lang = language_name(ms.language)
    if lang:
        system += f"\n\nWrite every insight's content in {lang}."

    user_parts = [f"Transcript so far:\n{transcript}"]
    meeting_ctx = meeting_context_text(ms.external_meeting)
    if meeting_ctx:
        user_parts.append(f"\nLinked meeting context:\n{meeting_ctx}")
    if topic_ctx:
        user_parts.append(f"\nAttached topic context:\n{topic_ctx}")
    notes_ctx = context_notes_text(ms.context_notes)
    if notes_ctx:
        user_parts.append(f"\nPinned context notes:\n{notes_ctx}")

    provider = await get_llm_provider(session)
    model = await resolve_live_fast_model(session)
    effort = await resolve_live_reasoning_effort(session)
    try:
        text, usage = await complete_text_with_usage(
            provider,
            model=model,
            messages=[
                ChatMessage(role="system", content=system),
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
