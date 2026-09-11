"""Generate, edit and merge a topic's editable status summary.

The summary is a short markdown brief kept above the transcript: where the
topic stands, what is still to do, and the handful of facts needed to pick it
back up. It is written by the model but owned by the user — once a summary has
been edited by hand, a regeneration never overwrites it. Instead the proposal
is parked in ``TopicSummary.pending_content`` and served as a list of
:class:`SummaryHunk` changes the user accepts or refuses one by one, the way a
code-suggestion review works.

The sections are fixed (``## Status`` / ``## Actions`` / ``## Key information``)
so ``/todo-summary`` and ``/important-summary`` can append to the right place
and so a regeneration produces a diff the user can read.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import Attachment, Message, MessageRole, NoteDraft, TopicSummary
from precursor.backend.services.app_settings import resolve_llm_model
from precursor.backend.services.llm import complete_text_with_usage, get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

#: Ledger source for the generation round-trip.
USAGE_SOURCE = "/update-summary"

STATUS_HEADING = "## Status"
ACTIONS_HEADING = "## Actions"
INFO_HEADING = "## Key information"

#: How many of the most recent turns feed the prompt, and how much of each.
_MAX_TURNS = 40
_MAX_TURN_CHARS = 2000
_MAX_NOTES_CHARS = 4000

_SYSTEM_BASE = (
    "You maintain a short status brief for a working topic. The brief is what "
    "someone reads to know where the topic stands and what is left to do.\n"
    "Rules:\n"
    "- Reply with GitHub-Flavored Markdown only — no preamble, no code fence "
    "around the whole answer.\n"
    "- Use exactly these sections, in this order, omitting none:\n"
    f"  {STATUS_HEADING} — 1 to 4 bullet points on where things stand.\n"
    f"  {ACTIONS_HEADING} — a task list (`- [ ]` pending, `- [x]` done) of "
    "real, actionable items with an owner when one is known. Leave it empty "
    "if there is genuinely nothing to do.\n"
    f"  {INFO_HEADING} — bullet points for the few facts, links or decisions "
    "needed to act (ids, versions, endpoints, constraints).\n"
    "- Only include actionable actions, updates and information. No filler, "
    "no restating the conversation, no summary of the summary.\n"
    "- Write in the language the conversation is in."
)

_PRESERVE_CLAUSE = (
    "\n- The existing brief below was written or edited by the user. Treat it "
    "as authoritative: keep its wording, ordering and items verbatim wherever "
    "they are still accurate, and change only what the conversation shows to "
    "be outdated, done or missing. Removing a user-written line requires "
    "evidence in the conversation that it no longer applies."
)


@dataclass(frozen=True)
class SummaryHunk:
    """One contiguous change between the live summary and a model proposal."""

    index: int
    #: Line offsets into the *current* content (`base`), for ordering/merging.
    base_start: int
    base_end: int
    removed: list[str]
    added: list[str]


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


def diff_hunks(base: str, proposed: str) -> list[SummaryHunk]:
    """Line-level changes turning ``base`` into ``proposed``.

    Each non-equal opcode becomes one reviewable hunk; equal runs are dropped.
    """
    base_lines = _lines(base)
    proposed_lines = _lines(proposed)
    matcher = difflib.SequenceMatcher(None, base_lines, proposed_lines, autojunk=False)
    hunks: list[SummaryHunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append(
            SummaryHunk(
                index=len(hunks),
                base_start=i1,
                base_end=i2,
                removed=base_lines[i1:i2],
                added=proposed_lines[j1:j2],
            )
        )
    return hunks


def apply_hunks(base: str, hunks: list[SummaryHunk], accepted: set[int]) -> str:
    """Rebuild the summary, taking the proposal only for accepted hunks."""
    base_lines = _lines(base)
    out: list[str] = []
    cursor = 0
    for hunk in hunks:
        out.extend(base_lines[cursor : hunk.base_start])
        if hunk.index in accepted:
            out.extend(hunk.added)
        else:
            out.extend(hunk.removed)
        cursor = hunk.base_end
    out.extend(base_lines[cursor:])
    return "\n".join(out).strip() + "\n" if any(line.strip() for line in out) else ""


def _split_sections(content: str) -> list[tuple[str | None, list[str]]]:
    """Split markdown into `(heading, body lines)` pairs, preamble first."""
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    body: list[str] = []
    for line in _lines(content):
        if line.startswith("## "):
            if heading is not None or any(b.strip() for b in body):
                sections.append((heading, body))
            heading = line
            body = []
        else:
            body.append(line)
    if heading is not None or any(b.strip() for b in body):
        sections.append((heading, body))
    return sections


def _render_sections(sections: list[tuple[str | None, list[str]]]) -> str:
    parts: list[str] = []
    for heading, body in sections:
        chunk = "\n".join(([heading] if heading else []) + body).strip("\n")
        if chunk.strip():
            parts.append(chunk)
    return "\n\n".join(parts).strip() + "\n" if parts else ""


def append_item(content: str, *, heading: str, item: str) -> str:
    """Append ``item`` under ``heading``, creating the section when missing.

    Used by ``/todo-summary`` (a pending action) and ``/important-summary``
    (a fact worth keeping), so a user can grow the brief without regenerating
    it. Duplicate lines are ignored so repeating a command is harmless.
    """
    line = item.strip()
    if not line:
        return content
    sections = _split_sections(content)
    for _, body in sections:
        if any(existing.strip() == line for existing in body):
            return _render_sections(sections)
    for position, (existing_heading, body) in enumerate(sections):
        if existing_heading == heading:
            trimmed = list(body)
            while trimmed and not trimmed[-1].strip():
                trimmed.pop()
            sections[position] = (existing_heading, [*trimmed, line])
            return _render_sections(sections)
    sections.append((heading, [line]))
    return _render_sections(sections)


def format_todo(text: str) -> str:
    """Normalise free text into an unchecked task-list line."""
    stripped = text.strip()
    if stripped.startswith(("- [ ]", "- [x]", "- [X]")):
        return stripped
    return f"- [ ] {stripped.removeprefix('- ').strip()}"


def format_important(text: str) -> str:
    """Normalise free text into a bullet line."""
    stripped = text.strip()
    if stripped.startswith("- "):
        return stripped
    return f"- {stripped}"


async def get_summary(session: AsyncSession, topic_id: int) -> TopicSummary | None:
    result = await session.execute(select(TopicSummary).where(TopicSummary.topic_id == topic_id))
    return result.scalar_one_or_none()


async def get_or_create_summary(session: AsyncSession, topic_id: int) -> TopicSummary:
    summary = await get_summary(session, topic_id)
    if summary is None:
        summary = TopicSummary(topic_id=topic_id, content="", visible=True)
        session.add(summary)
        await session.flush()
    return summary


async def build_context(session: AsyncSession, topic_id: int, title: str) -> str:
    """Assemble the material the summary is generated from.

    The conversation, plus the topic's scratchpad notes and the names of the
    files attached to it — the three sources the issue asks the refresh to
    consider.
    """
    result = await session.execute(
        select(Message)
        .where(Message.topic_id == topic_id, Message.role != MessageRole.TOOL)
        .order_by(Message.id.desc())
        .limit(_MAX_TURNS)
    )
    messages = list(reversed(result.scalars().all()))

    parts: list[str] = [f"Topic: {title}"]
    transcript: list[str] = []
    for message in messages:
        content = (message.content or "").strip()
        if not content:
            continue
        if len(content) > _MAX_TURN_CHARS:
            content = f"{content[:_MAX_TURN_CHARS]}…"
        transcript.append(f"{message.role.value}: {content}")
    parts.append("Conversation:\n" + ("\n\n".join(transcript) or "(no messages yet)"))

    note = (
        await session.execute(select(NoteDraft).where(NoteDraft.topic_id == topic_id))
    ).scalar_one_or_none()
    if note is not None and note.text.strip():
        parts.append("Notes scratchpad:\n" + note.text.strip()[:_MAX_NOTES_CHARS])

    attachments = (
        (
            await session.execute(
                select(Attachment)
                .where(Attachment.topic_id == topic_id, Attachment.message_id.is_not(None))
                .order_by(Attachment.id)
            )
        )
        .scalars()
        .all()
    )
    names = [a.original_filename for a in attachments if a.original_filename]
    if names:
        parts.append("Attached files: " + ", ".join(names))

    return "\n\n".join(parts)


async def generate_summary(
    session: AsyncSession,
    *,
    topic_id: int,
    title: str,
    existing: str,
    preserve: bool,
    instruction: str | None = None,
) -> tuple[str, str]:
    """Ask the model for a brief. Returns ``(markdown, model)``.

    ``preserve`` raises the weight of ``existing``: when the user has edited
    the brief by hand, the prompt makes its content authoritative so a refresh
    keeps it wherever it is still relevant.
    """
    system = _SYSTEM_BASE + (_PRESERVE_CLAUSE if preserve and existing.strip() else "")
    user_parts = [await build_context(session, topic_id, title)]
    if existing.strip():
        label = "Existing brief (user-edited)" if preserve else "Existing brief"
        user_parts.append(f"{label}:\n{existing.strip()}")
    if instruction and instruction.strip():
        user_parts.append(f"Extra instruction from the user: {instruction.strip()}")

    provider = await get_llm_provider(session)
    model = await resolve_llm_model(session)
    text, usage = await complete_text_with_usage(
        provider,
        model=model,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="\n\n".join(user_parts)),
        ],
    )
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
    return sanitize_summary(text), model


def sanitize_summary(raw: str) -> str:
    """Strip a whole-answer code fence and normalise trailing whitespace."""
    text = (raw or "").replace("\r\n", "\n").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return text


def utcnow() -> datetime:
    return datetime.now(UTC)
