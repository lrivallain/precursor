"""Refine with AI — rewrite a block of user-authored text.

A small utility the frontend calls from the "Refine with AI" affordance next to
textareas. It rewrites the given text for clarity and correctness while keeping
the meaning, intent, language, and any Markdown intact. The round-trip's token
usage is written to the usage ledger like every other metered LLM call.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.services.app_settings import resolve_llm_model
from precursor.backend.services.llm import complete_text_with_usage, get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

# Cap the input so a runaway paste can't blow up the prompt budget.
_MAX_CHARS = 16000

_BASE_SYSTEM = (
    "You are an editor. Rewrite the user's text so it reads clearly and "
    "correctly. Fix grammar, spelling, and awkward phrasing, and improve "
    "structure and flow. Preserve the original meaning, intent, tone, and "
    "language — do not translate. Keep any Markdown, code, links, and "
    "placeholders intact. Do not add new facts, headings, or commentary. "
    "Return only the rewritten text — no preamble, no explanation, no "
    "surrounding quotes."
)

# Per-kind guidance appended to the base system prompt. Keys mirror the
# ``refineKind`` values the frontend passes from each textarea.
_KIND_GUIDANCE: dict[str, str] = {
    "chat_message": (
        "The text is a message the user will send to an AI assistant. Make it a "
        "clear, specific request. Keep it concise."
    ),
    "system_prompt": (
        "The text is a system prompt / persona instruction for an AI assistant. "
        "Make it clear, specific, and well-structured while preserving every "
        "requirement and constraint."
    ),
    "instructions": (
        "The text is a set of instructions for an AI assistant or command. Make "
        "it clear, specific, and unambiguous while preserving every requirement."
    ),
    "scheduled_prompt": (
        "The text is a prompt that will run automatically on a schedule. Make it "
        "a clear, self-contained instruction. Leave any leading slash command "
        "(e.g. /agent) unchanged."
    ),
    "note": "The text is a personal note. Keep it faithful and concise.",
    "summary": (
        "The text is a Markdown summary. Keep the section structure and only tighten wording."
    ),
    "comment": (
        "The text is a comment on a GitHub issue or pull request. Keep a "
        "professional, constructive tone."
    ),
    "description": ("The text is a short description. Keep it concise — one or two sentences."),
    "memory": (
        "The text is a single remembered fact about the user or project. Keep it "
        "a concise factual statement; do not embellish."
    ),
}


def _build_system(kind: str | None, instruction: str | None) -> str:
    parts = [_BASE_SYSTEM]
    guidance = _KIND_GUIDANCE.get(kind or "")
    if guidance:
        parts.append(guidance)
    if instruction and instruction.strip():
        parts.append(f"Additional instruction from the user: {instruction.strip()}")
    return "\n\n".join(parts)


async def refine_text(
    session: AsyncSession,
    *,
    text: str,
    kind: str | None = None,
    instruction: str | None = None,
) -> tuple[str, str]:
    """Rewrite ``text`` with the configured LLM. Returns ``(refined, model)``."""
    source = text.strip()[:_MAX_CHARS]
    if not source:
        return "", ""

    provider = await get_llm_provider(session)
    model = await resolve_llm_model(session)
    refined, usage = await complete_text_with_usage(
        provider,
        model=model,
        messages=[
            ChatMessage(role="system", content=_build_system(kind, instruction)),
            ChatMessage(role="user", content=source),
        ],
    )
    if usage is not None:
        await record_usage(
            session,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            source="/refine",
            model=model,
        )
        await session.commit()
    # Fall back to the original text if the model returned nothing usable.
    return (refined or source), model
