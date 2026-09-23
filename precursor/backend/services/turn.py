"""Run a single conversational turn outside the HTTP/SSE path.

The chat router streams turns to the browser over SSE. Scheduled topics need
the *same* generation logic (system context, history hydration, MCP tool loop,
message persistence) but driven by the background scheduler instead of a request.

Rather than duplicate that logic, this reuses the shared pieces: the turn is
prepared by :mod:`precursor.backend.services.conversation_turn`, and
:func:`~precursor.backend.services.turn_engine.run_tool_loop` drives the provider
+ MCP tool loop. This module only skips the streaming: it stores each round with
the same persistence helpers the SSE consumer uses and emits the same
``stream.started`` / ``stream.ended`` / ``message.changed`` events, so the UI
lights up exactly as it does for a manual chat.
"""

from __future__ import annotations

import logging
import time

import anyio

from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole, Topic
from precursor.backend.services.conversation_turn import (
    clear_container_messages,
    persist_user_message,
    resolve_turn_settings,
    snapshot_history,
)
from precursor.backend.services.events import (
    publish_message_changed,
    publish_stream_ended,
    publish_stream_started,
)
from precursor.backend.services.mcp.client import get_mcp_client_manager
from precursor.backend.services.turn_engine import (
    AssistantFinalTurn,
    AssistantTextDelta,
    AssistantToolCallsTurn,
    RoundCapReached,
    ToolAuthRequired,
    ToolResultTurn,
    build_system_context,
    persist_final_turn,
    persist_tool_calls_turn,
    persist_tool_result,
    run_tool_loop,
)

logger = logging.getLogger(__name__)


async def run_topic_turn(
    topic_id: int,
    prompt: str,
    *,
    clear_context: bool = False,
    llm_prompt: str | None = None,
) -> None:
    """Persist ``prompt`` as a user turn and generate the assistant reply.

    When ``clear_context`` is set, the topic's prior messages are deleted first
    so the run starts from a clean slate. Runs the full MCP tool loop. Raises on
    provider failure so the caller (the scheduler) can record the error;
    partial messages already persisted stay.

    ``llm_prompt`` lets a skill invocation persist the literal slash command as
    the user turn while sending the expanded instructions to the LLM for this
    turn only (the same ``prompt_override`` patch the stream routers apply, via
    :func:`~precursor.backend.services.conversation_turn.snapshot_history`).
    """
    await publish_stream_started(topic_id)
    try:
        await _run(topic_id, prompt, clear_context=clear_context, llm_prompt=llm_prompt)
    finally:
        await publish_stream_ended(topic_id)


async def _run(
    topic_id: int,
    prompt: str,
    *,
    clear_context: bool = False,
    llm_prompt: str | None = None,
) -> None:
    manager = get_mcp_client_manager()

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            logger.warning("Scheduled run for missing topic %s", topic_id)
            return

        # Optionally wipe prior turns so each run is independent of history.
        if clear_context:
            await clear_container_messages(session, "topic", topic_id)

        # Persist the scheduled prompt as the user turn so the transcript and
        # the unread badge behave like a normal conversation.
        await persist_user_message(session, "topic", topic_id, prompt)

        system_prompt = await build_system_context(session, topic)
        history = await snapshot_history(session, "topic", topic_id, prompt_override=llm_prompt)
        # Never let a programmatically-driven turn (scheduler / MCP post_message)
        # re-expose Precursor's own MCP server to itself — that would let a
        # post_message-triggered turn recursively call post_message.
        settings = await resolve_turn_settings(session, exclude_servers={"precursor"})

    async with manager.acquired(
        settings.enabled_servers, github_token=settings.github_token, advertise_cached=True
    ) as active:
        for server_name, err in active.unavailable:
            logger.warning("Scheduled run: MCP server %s unavailable: %s", server_name, err)

        turn_started = time.monotonic()
        async for ev in run_tool_loop(
            active=active,
            provider=settings.provider,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            system_prompt=system_prompt,
            history=history,
            max_tool_rounds=settings.max_tool_rounds,
            max_input_tokens=settings.max_input_tokens,
            max_tool_result_tokens=settings.max_tool_result_tokens,
            # Unattended: nobody is watching to complete a browser sign-in, so a
            # tool that needs one raises the app-global banner and fails fast
            # rather than parking the run for the full interactive window.
            auth_wait_timeout=0.0,
        ):
            if isinstance(ev, AssistantTextDelta):
                # The scheduler doesn't stream deltas; it persists whole turns.
                continue

            if isinstance(ev, ToolAuthRequired):
                from precursor.backend.services.events import publish_mcp_auth_required

                logger.warning("Scheduled run: %s needs a sign-in for %s", ev.server, ev.tool)
                await publish_mcp_auth_required(ev.server, ev.message, topic_id=topic_id)
                continue

            # Stored exactly as a streamed turn stores them (clean text + chips,
            # model, elapsed time, and a usage-ledger row per metered round).
            if isinstance(ev, AssistantFinalTurn):
                elapsed_ms = int((time.monotonic() - turn_started) * 1000)
                await persist_final_turn(
                    "topic", topic_id, ev, model=settings.model, elapsed_ms=elapsed_ms
                )
                return

            if isinstance(ev, AssistantToolCallsTurn):
                await persist_tool_calls_turn("topic", topic_id, ev, model=settings.model)

            elif isinstance(ev, ToolResultTurn):
                await persist_tool_result("topic", topic_id, ev)

            elif isinstance(ev, RoundCapReached):
                # Exhausted the tool-round budget without a final answer. This
                # stays the unattended turn's own assistant note; the stream
                # records an "Error: …" system row instead.
                async with SessionLocal() as ws:
                    ws.add(
                        Message(
                            topic_id=topic_id,
                            role=MessageRole.ASSISTANT,
                            content=f"(Stopped after {ev.max_tool_rounds} tool rounds.)",
                        )
                    )
                    await ws.commit()
                await publish_message_changed(topic_id)


# Re-exported for the scheduler's timeout wrapper.
__all__ = ["run_topic_turn"]


async def run_topic_turn_with_timeout(
    topic_id: int, prompt: str, timeout: float, *, clear_context: bool = False
) -> None:
    # Use anyio's cancel scope rather than asyncio.timeout: the MCP client
    # sessions opened inside run_topic_turn rely on anyio task groups, and an
    # asyncio.timeout cancel scope wrapped around them raises spurious
    # "unhandled errors in a TaskGroup" on exit. anyio.fail_after composes
    # correctly and raises TimeoutError, which the scheduler already handles.
    with anyio.fail_after(timeout):
        await run_topic_turn(topic_id, prompt, clear_context=clear_context)
