"""Context compaction for topics and chats.

A long conversation eventually fills the model's context window, and
``context_budget.trim_messages`` then silently drops its oldest turns. Compaction
replaces them with a summary instead: the model writes a dense recap of the
conversation so far, stored as a SYSTEM row with ``kind="compaction"``.
``hydrate_history`` starts every later turn at the latest such marker, so the
model sees the summary plus what came after it.

It is non-destructive: the older rows stay in the transcript for the reader,
and deleting the marker restores the full history for the model. Compacting
again folds the previous summary into the new one.

:func:`estimate_context` reports what the next turn would send, so the UI can
say how full the window is before and after compacting.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.db import SessionLocal
from precursor.backend.models import MESSAGE_KIND_COMPACTION, Message, MessageRole
from precursor.backend.services.app_settings import (
    resolve_llm_max_tool_result_tokens,
    resolve_llm_tool_result_keep_turns,
)
from precursor.backend.services.context_budget import (
    elide_stale_tool_results,
    estimate_tokens,
    strip_inline_binaries,
)
from precursor.backend.services.conversation_turn import load_container_rows, load_model_history
from precursor.backend.services.events import is_streaming
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.llm.one_shot import complete_once
from precursor.backend.services.model_fallbacks import resolve_llm_preparation_budget
from precursor.backend.services.turn_engine import (
    ContainerKind,
    container_message_kwargs,
    publish_container_changed,
)

USAGE_SOURCE = "/compact"

# Characters kept per message when rendering the transcript for the summariser:
# enough for the facts that matter, not a whole file read.
_TOOL_RESULT_CHARS = 8_000
_MESSAGE_CHARS = 40_000
# Fraction of the input budget one summariser call may use; the rest is headroom
# for the prompt, the running summary and the answer.
_CHUNK_BUDGET_RATIO = 0.6
_CHARS_PER_TOKEN = 3

_SYSTEM = """\
You compact a conversation between a user and an AI assistant so it can go on \
in a fresh context window without the original messages.

Write a dense Markdown summary with these sections (skip one only if empty):

## Goal and context
What the user is trying to achieve, and the background that matters.

## Key facts and decisions
Names, IDs, numbers, dates, URLs, file paths, commands and conclusions — \
copied verbatim, never paraphrased.

## Work done
What was investigated or produced, including what tool calls returned that \
still matters. Drop raw output that no longer does.

## Open questions and pending items

## Current state and next step
The user's latest request and exactly where the assistant left off.

Rules: write in the conversation's language; never invent anything; no \
preamble or closing remarks — output only the summary."""


class CompactionError(Exception):
    """Compaction can't run right now; ``str()`` is the user-facing reason."""


@dataclass(frozen=True, slots=True)
class ContextEstimate:
    """What the next turn would send the model, in estimated tokens."""

    tokens: int
    by_role: dict[str, int]
    # Tokens the hygiene passes (binary stripping, stale-result elision) save.
    saved_by_hygiene: int
    messages: int
    compacted: bool
    compaction_id: int | None
    # Rows the latest marker hides from the model.
    compacted_messages: int


def render_transcript(history: list[ChatMessage]) -> list[str]:
    """One text block per message, capped, for the summariser to read."""
    blocks: list[str] = []
    for m in history:
        content = m.content or ""
        if m.role == "tool":
            content = strip_inline_binaries(content)
            head = f"### Tool result ({m.name or 'tool'})"
            limit = _TOOL_RESULT_CHARS
        else:
            head = f"### {m.role.capitalize()}"
            limit = _MESSAGE_CHARS
        if len(content) > limit:
            content = content[:limit] + f"\n…[cut: {len(content) - limit:,} more characters]"
        if m.role == "assistant" and m.tool_calls:
            names = ", ".join(
                str((c.get("function") or {}).get("name") or "?")
                for c in m.tool_calls
                if isinstance(c, dict)
            )
            content = f"{content}\n(called tools: {names})".strip()
        if m.image_urls:
            content = f"{content}\n({len(m.image_urls)} image(s) attached)".strip()
        blocks.append(f"{head}\n{content}")
    return blocks


def chunk_blocks(blocks: list[str], max_chars: int) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > max_chars:
            out.append("\n\n".join(current))
            current, size = [], 0
        current.append(block[:max_chars])
        size += len(block)
    if current:
        out.append("\n\n".join(current))
    return out


async def compact_container(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    *,
    instructions: str | None = None,
) -> Message:
    """Summarise the conversation so far and append a compaction marker.

    Raises :class:`CompactionError` while a turn is generating or when there is
    nothing new to compact, and ``LLMCallFailed`` when the model call fails.
    """
    if is_streaming(kind, container_id):
        raise CompactionError("A reply is still being generated; wait for it to finish.")
    history = await load_model_history(session, kind, container_id)
    if not any(m.role in ("user", "assistant") for m in history):
        raise CompactionError("Nothing to compact yet.")

    budget_tokens = await resolve_llm_preparation_budget(session)
    chunk_chars = int(budget_tokens * _CHUNK_BUDGET_RATIO) * _CHARS_PER_TOKEN
    chunks = chunk_blocks(render_transcript(history), chunk_chars)
    focus = (instructions or "").strip()
    fk = container_message_kwargs(kind, container_id)

    summary = ""
    model = ""
    prompt_tokens = 0
    completion_tokens = 0
    for idx, chunk in enumerate(chunks):
        parts: list[str] = []
        if summary:
            parts.append(
                "Summary of the conversation so far (fold it into your new summary):\n\n" + summary
            )
        label = (
            "Conversation" if len(chunks) == 1 else f"Conversation, part {idx + 1}/{len(chunks)}"
        )
        parts.append(f"{label}:\n\n{chunk}")
        if focus:
            parts.append(f"Extra focus requested by the user: {focus}")
        result = await complete_once(
            session,
            system=_SYSTEM,
            user="\n\n---\n\n".join(parts),
            usage_source=USAGE_SOURCE,
            topic_id=container_id if kind == "topic" else None,
            chat_id=container_id if kind == "chat" else None,
        )
        summary, model = result.text.strip(), result.model
        if result.usage is not None:
            prompt_tokens += result.usage.prompt_tokens
            completion_tokens += result.usage.completion_tokens
    if not summary:
        raise CompactionError("The model returned an empty summary.")

    # Written in a fresh session: complete_once committed the caller's, and the
    # model call can outlast a request-scoped connection.
    async with SessionLocal() as ws:
        marker = Message(
            role=MessageRole.SYSTEM,
            kind=MESSAGE_KIND_COMPACTION,
            content=summary,
            model=model or None,
            prompt_tokens=prompt_tokens or None,
            completion_tokens=completion_tokens or None,
            **fk,
        )
        ws.add(marker)
        await ws.commit()
        loaded = (
            await ws.execute(
                select(Message)
                .where(Message.id == marker.id)
                .options(selectinload(Message.attachments))
            )
        ).scalar_one()
    await publish_container_changed(kind, container_id)
    return loaded


async def estimate_context(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> ContextEstimate:
    """Estimate the history the next turn sends (system prompt and tools excluded)."""
    rows = await load_container_rows(session, kind, container_id)
    marker_idx = next(
        (i for i in range(len(rows) - 1, -1, -1) if rows[i].kind == MESSAGE_KIND_COMPACTION),
        None,
    )
    raw = await load_model_history(session, kind, container_id)
    keep_turns = await resolve_llm_tool_result_keep_turns(session)
    per_message_chars = await resolve_llm_max_tool_result_tokens(session) * _CHARS_PER_TOKEN
    sent = elide_stale_tool_results(raw, keep_turns=keep_turns)

    def _cost(m: ChatMessage) -> int:
        if m.role != "tool":
            return estimate_tokens(m)
        content = strip_inline_binaries(m.content or "")[:per_message_chars]
        return estimate_tokens(ChatMessage(role="tool", content=content, name=m.name))

    by_role: dict[str, int] = {}
    for m in sent:
        by_role[m.role] = by_role.get(m.role, 0) + _cost(m)
    total = sum(by_role.values())
    return ContextEstimate(
        tokens=total,
        by_role=by_role,
        saved_by_hygiene=max(0, sum(estimate_tokens(m) for m in raw) - total),
        messages=len(sent),
        compacted=marker_idx is not None,
        compaction_id=rows[marker_idx].id if marker_idx is not None else None,
        compacted_messages=marker_idx or 0,
    )
