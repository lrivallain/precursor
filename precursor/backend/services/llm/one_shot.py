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

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.db import SessionLocal
from precursor.backend.services.app_settings import resolve_llm_model
from precursor.backend.services.llm import complete_text_with_usage, get_llm_provider
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMProvider,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    ToolDef,
    UsageEvent,
)
from precursor.backend.services.model_fallbacks import resolve_model_category
from precursor.backend.services.usage_stats import record_usage

__all__ = ["LLMCallFailed", "OneShotResult", "complete_once", "complete_with_tools"]


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


async def _resolve(
    session: AsyncSession,
    *,
    usage_source: str,
    topic_id: int | None,
    chat_id: int | None,
    model: str | None,
    provider_id: str | None,
    reasoning_effort: str | None,
    use_conversation_selection: bool,
) -> tuple[LLMProvider, str, str | None]:
    """The provider, model and reasoning effort a one-shot call runs with."""
    from precursor.backend.services.conversation_llm import resolve_selection, saved_selection

    selection = (
        await saved_selection(session, topic_id=topic_id, chat_id=chat_id)
        if use_conversation_selection
        else None
    )
    resolved_model = model or await resolve_llm_model(session) if selection is None else ""
    try:
        if selection is not None:
            resolved = await resolve_selection(session, selection)
            provider = resolved.provider
            resolved_model = resolved.model
            reasoning_effort = resolved.reasoning_effort
        else:
            category = await resolve_model_category(session) if not model else None
            if model and provider_id:
                provider = await get_llm_provider(
                    session, override_provider=provider_id, strict=True
                )
            elif category is not None:
                provider = await get_llm_provider(session, model_category=category)
            else:
                provider = await get_llm_provider(session)
    except Exception as exc:
        raise LLMCallFailed(usage_source, exc) from exc
    return provider, resolved_model, reasoning_effort


async def complete_once(
    session: AsyncSession,
    *,
    system: str,
    user: str,
    usage_source: str,
    topic_id: int | None = None,
    chat_id: int | None = None,
    model: str | None = None,
    provider_id: str | None = None,
    reasoning_effort: str | None = None,
    release_connection: bool = True,
    use_conversation_selection: bool = True,
) -> OneShotResult:
    """Run a tool-less completion and record its token usage.

    ``model`` defaults to the chat model; pass a feature-specific one (the live
    fast model, the auto-name model) when the feature has its own setting. With
    a ``model``, ``provider_id`` serves it from that provider instead of the
    global default (it is ignored without one: the chat model belongs to the
    default provider).

    With ``release_connection`` the caller's session is committed before the
    provider call so a slow model doesn't keep a pooled connection checked out.
    That also commits anything the caller left pending, so a caller with
    uncommitted writes it must not persist yet has to pass ``False``.

    Usage is written in its own session, so it lands even when the caller later
    rejects the output or rolls back.
    """
    provider, resolved_model, reasoning_effort = await _resolve(
        session,
        usage_source=usage_source,
        topic_id=topic_id,
        chat_id=chat_id,
        model=model,
        provider_id=provider_id,
        reasoning_effort=reasoning_effort,
        use_conversation_selection=use_conversation_selection,
    )
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

    resolved_model = getattr(provider, "effective_model", None) or resolved_model
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


_OUT_OF_ROUNDS = (
    "No more tool calls are available. Give your final answer now, in the format asked for."
)


async def complete_with_tools(
    session: AsyncSession,
    *,
    system: str,
    user: str,
    usage_source: str,
    tools: Sequence[ToolDef],
    run_tool: Callable[[ToolCallRequest], Awaitable[str]],
    max_rounds: int = 6,
    topic_id: int | None = None,
    model: str | None = None,
    provider_id: str | None = None,
    reasoning_effort: str | None = None,
    use_conversation_selection: bool = False,
) -> OneShotResult:
    """Run a short, bounded tool loop and return the model's final text.

    For a caller that wants the model to inspect something before it answers (the
    step assessor reading a file). ``run_tool`` executes one call and returns the
    text the model sees; it must not raise for a bad call, but report it as text.
    After ``max_rounds`` rounds the tools are withdrawn so the model has to
    answer. Each round's usage is recorded in its own session.
    """
    provider, resolved_model, reasoning_effort = await _resolve(
        session,
        usage_source=usage_source,
        topic_id=topic_id,
        chat_id=None,
        model=model,
        provider_id=provider_id,
        reasoning_effort=reasoning_effort,
        use_conversation_selection=use_conversation_selection,
    )
    await session.commit()
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]
    last_usage: UsageEvent | None = None
    text = ""
    try:
        for round_no in range(max_rounds + 1):
            final_round = round_no == max_rounds
            if final_round:
                messages.append(ChatMessage(role="user", content=_OUT_OF_ROUNDS))
            chunks: list[str] = []
            calls: list[ToolCallRequest] = []
            usage: UsageEvent | None = None
            async for event in provider.stream_chat_with_tools(
                model=resolved_model,
                messages=messages,
                tools=[] if final_round else tools,
                reasoning_effort=reasoning_effort or None,
            ):
                if isinstance(event, TextDeltaEvent):
                    chunks.append(event.content)
                elif isinstance(event, ToolCallsEvent):
                    calls = event.calls
                elif isinstance(event, UsageEvent):
                    usage = event
            if usage is not None:
                last_usage = usage
                await _record(usage, usage_source, provider, resolved_model, topic_id)
            text = "".join(chunks)
            if not calls or final_round:
                break
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=text,
                    tool_calls=[
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments},
                        }
                        for c in calls
                    ],
                )
            )
            for call in calls:
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=await run_tool(call),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
    except Exception as exc:
        raise LLMCallFailed(usage_source, exc) from exc
    resolved_model = getattr(provider, "effective_model", None) or resolved_model
    return OneShotResult(text=text, model=resolved_model, usage=last_usage)


async def _record(
    usage: UsageEvent,
    source: str,
    provider: LLMProvider,
    model: str,
    topic_id: int | None,
) -> None:
    async with SessionLocal() as usage_session:
        await record_usage(
            usage_session,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            source=source,
            model=getattr(provider, "effective_model", None) or model,
            topic_id=topic_id,
            chat_id=None,
        )
        await usage_session.commit()
