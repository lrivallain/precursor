"""Run a single conversational turn outside the HTTP/SSE path.

The chat router streams turns to the browser over SSE. Scheduled topics need
the *same* generation logic (system context, history hydration, MCP tool loop,
message persistence) but driven by the background scheduler instead of a request.

Rather than duplicate that logic, this reuses the shared engine in
:mod:`precursor.backend.services.turn_engine`: :func:`run_tool_loop` drives the
provider + MCP tool loop and yields semantic events, and this module applies the
scheduler's own (plain, non-streaming) persistence policy on top, emitting the
same ``stream.started`` / ``stream.ended`` / ``message.changed`` events so the UI
lights up exactly as it does for a manual chat.
"""

from __future__ import annotations

import json
import logging

import anyio
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole, Topic
from precursor.backend.services.app_settings import (
    resolve_llm_max_input_tokens,
    resolve_llm_max_tool_result_tokens,
    resolve_llm_model,
    resolve_llm_reasoning_effort,
    resolve_max_tool_rounds,
)
from precursor.backend.services.events import (
    publish_message_changed,
    publish_stream_ended,
    publish_stream_started,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import ChatMessage
from precursor.backend.services.mcp.client import get_mcp_client_manager
from precursor.backend.services.turn_engine import (
    AssistantFinalTurn,
    AssistantTextDelta,
    AssistantToolCallsTurn,
    RoundCapReached,
    ToolResultTurn,
    build_system_context,
    hydrate_history,
    load_enabled_mcp_servers,
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
    turn only (mirrors the ``prompt_override`` path in ``routers/chat.py``).
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
            await session.execute(delete(Message).where(Message.topic_id == topic_id))
            await session.commit()

        # Persist the scheduled prompt as the user turn so the transcript and
        # the unread badge behave like a normal conversation.
        user_msg = Message(topic_id=topic_id, role=MessageRole.USER, content=prompt)
        session.add(user_msg)
        await session.commit()
        await publish_message_changed(topic_id)

        system_prompt = await build_system_context(session, topic)
        history_result = await session.execute(
            select(Message)
            .where(Message.topic_id == topic_id)
            .options(selectinload(Message.attachments))
            .order_by(Message.created_at)
        )
        history = hydrate_history(list(history_result.scalars().all()))
        # For skill invocations the persisted user turn stays the literal slash
        # command, but the LLM should see the expanded prompt for this turn only.
        if llm_prompt is not None:
            for idx in range(len(history) - 1, -1, -1):
                if history[idx].role == "user":
                    history[idx] = ChatMessage(
                        role="user",
                        content=llm_prompt,
                        image_urls=history[idx].image_urls,
                    )
                    break
        enabled_servers = await load_enabled_mcp_servers(session)
        # Never let a programmatically-driven turn (scheduler / MCP post_message)
        # re-expose Precursor's own MCP server to itself — that would let a
        # post_message-triggered turn recursively call post_message.
        enabled_servers = [s for s in enabled_servers if s != "precursor"]
        model = await resolve_llm_model(session)
        reasoning_effort = await resolve_llm_reasoning_effort(session)
        max_tool_rounds = await resolve_max_tool_rounds(session)
        max_input_tokens = await resolve_llm_max_input_tokens(session)
        max_tool_result_tokens = await resolve_llm_max_tool_result_tokens(session)
        provider = await get_llm_provider(session)
        github_token = await resolve_github_token(session)

    async with manager.acquired(enabled_servers, github_token=github_token) as active:
        for server_name, err in active.unavailable:
            logger.warning("Scheduled run: MCP server %s unavailable: %s", server_name, err)

        async for ev in run_tool_loop(
            active=active,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            system_prompt=system_prompt,
            history=history,
            max_tool_rounds=max_tool_rounds,
            max_input_tokens=max_input_tokens,
            max_tool_result_tokens=max_tool_result_tokens,
        ):
            if isinstance(ev, AssistantTextDelta):
                # The scheduler doesn't stream deltas; it persists whole turns.
                continue

            if isinstance(ev, AssistantFinalTurn):
                round_usage = ev.usage
                async with SessionLocal() as ws:
                    ws.add(
                        Message(
                            topic_id=topic_id,
                            role=MessageRole.ASSISTANT,
                            content=ev.text,
                            prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                            completion_tokens=round_usage.completion_tokens
                            if round_usage
                            else None,
                        )
                    )
                    await ws.commit()
                await publish_message_changed(topic_id)
                return

            if isinstance(ev, AssistantToolCallsTurn):
                round_usage = ev.usage
                async with SessionLocal() as ws:
                    ws.add(
                        Message(
                            topic_id=topic_id,
                            role=MessageRole.ASSISTANT,
                            content=ev.text,
                            tool_calls=json.dumps(ev.openai_tool_calls),
                            prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                            completion_tokens=round_usage.completion_tokens
                            if round_usage
                            else None,
                        )
                    )
                    await ws.commit()
                await publish_message_changed(topic_id)

            elif isinstance(ev, ToolResultTurn):
                call = ev.call
                async with SessionLocal() as ws:
                    ws.add(
                        Message(
                            topic_id=topic_id,
                            role=MessageRole.TOOL,
                            content=ev.result_text,
                            tool_calls=json.dumps(
                                {
                                    "tool_call_id": call.id,
                                    "name": call.name,
                                    "arguments": call.arguments,
                                    "is_error": ev.is_error,
                                }
                            ),
                        )
                    )
                    await ws.commit()
                await publish_message_changed(topic_id)

            elif isinstance(ev, RoundCapReached):
                # Exhausted the tool-round budget without a final answer.
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
