"""Session-aware wrapper for "ask the model once, no tools" features.

Slash-command drafts, ``/refine``, auto-naming, the topic brief and the live
meeting recap, analysis and translation all make one tool-less round-trip.
:func:`complete_once` owns the part they share: provider and model resolution,
connection hygiene, usage accounting and a single error type. Prompt building,
output sanitising and fallback rules stay with each caller.

:func:`~precursor.backend.services.llm.complete_text_with_usage` stays
provider-level and session-free; this module is the thin DB-aware layer on top.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.services.app_settings import resolve_llm_model
from precursor.backend.services.llm import complete_text_with_usage, get_llm_provider
from precursor.backend.services.llm.base import ChatMessage, UsageEvent
from precursor.backend.services.usage_stats import record_usage

__all__ = ["LLMCallFailed", "OneShotResult", "complete_once"]


@dataclass(frozen=True, slots=True)
class OneShotResult:
    text: str
    model: str
    usage: UsageEvent | None


class LLMCallFailed(Exception):
    """The provider could not be reached or the completion failed.

    ``str()`` is the underlying error so callers that add their own prefix
    ("Summary failed: …") don't stack two. The app maps an uncaught instance to
    a 502 (see ``main.create_app``).
    """

    def __init__(self, source: str, cause: BaseException) -> None:
        super().__init__(str(cause) or type(cause).__name__)
        self.source = source

    @property
    def detail(self) -> str:
        """User-facing message, mirroring ``HTTPException.detail``."""
        return f"LLM call failed: {self}"


async def complete_once(
    session: AsyncSession,
    *,
    system: str,
    user: str,
    usage_source: str,
    topic_id: int | None = None,
    chat_id: int | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    release_connection: bool = True,
) -> OneShotResult:
    """Run a tool-less completion and record its token usage.

    ``model`` defaults to the chat model; pass a feature-specific one (the live
    fast model, the auto-name model) when the feature has its own setting.

    With ``release_connection`` the caller's session is committed before the
    provider call so a slow model doesn't keep a pooled connection checked out.
    That also commits anything the caller left pending, so a caller with
    uncommitted writes it must not persist yet has to pass ``False``.

    Usage is written in its own session, so it lands even when the caller later
    rejects the output or rolls back.
    """
    resolved_model = model or await resolve_llm_model(session)
    try:
        provider = await get_llm_provider(session)
    except Exception as exc:
        raise LLMCallFailed(usage_source, exc) from exc
    if release_connection:
        await session.commit()
    try:
        text, usage = await complete_text_with_usage(
            provider,
            model=resolved_model,
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
            reasoning_effort=reasoning_effort or None,
        )
    except Exception as exc:
        raise LLMCallFailed(usage_source, exc) from exc

    if usage is not None:
        async with SessionLocal() as usage_session:
            await record_usage(
                usage_session,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                source=usage_source,
                model=resolved_model,
                topic_id=topic_id,
                chat_id=chat_id,
            )
            await usage_session.commit()
    return OneShotResult(text=text, model=resolved_model, usage=usage)
