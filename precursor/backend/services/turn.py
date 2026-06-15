"""Run a single conversational turn outside the HTTP/SSE path.

The chat router streams turns to the browser over SSE. Scheduled topics need
the *same* generation logic (system context, history hydration, MCP tool loop,
message persistence) but driven by the background scheduler instead of a request.

Rather than duplicate that logic, we reuse the pure helpers from the chat
router and replay the tool loop here, persisting messages and emitting the same
``stream.started`` / ``stream.ended`` / ``message.changed`` events so the UI
lights up exactly as it does for a manual chat.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any

import anyio
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole, Topic
from precursor.backend.routers.chat import (
    _build_system_context,
    _format_tool_result,
    _hydrate_history,
    _load_enabled_mcp_servers,
    _mcp_tools_to_provider,
)
from precursor.backend.services.app_settings import (
    resolve_llm_max_input_tokens,
    resolve_llm_max_tool_result_tokens,
    resolve_llm_model,
    resolve_max_tool_rounds,
)
from precursor.backend.services.context_budget import trim_messages
from precursor.backend.services.events import (
    publish_message_changed,
    publish_stream_ended,
    publish_stream_started,
)
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import (
    ChatMessage,
    TextDeltaEvent,
    ToolCallsEvent,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.mcp.client import MCPToolDef, get_mcp_client_manager

logger = logging.getLogger(__name__)


async def run_topic_turn(topic_id: int, prompt: str, *, clear_context: bool = False) -> None:
    """Persist ``prompt`` as a user turn and generate the assistant reply.

    When ``clear_context`` is set, the topic's prior messages are deleted first
    so the run starts from a clean slate. Runs the full MCP tool loop. Raises on
    provider failure so the caller (the scheduler) can record the error;
    partial messages already persisted stay.
    """
    await publish_stream_started(topic_id)
    try:
        await _run(topic_id, prompt, clear_context=clear_context)
    finally:
        await publish_stream_ended(topic_id)


async def _run(topic_id: int, prompt: str, *, clear_context: bool = False) -> None:
    settings = get_settings()
    manager = get_mcp_client_manager()
    provider = get_llm_provider()

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

        system_prompt = await _build_system_context(session, topic)
        history_result = await session.execute(
            select(Message)
            .where(Message.topic_id == topic_id)
            .options(selectinload(Message.attachments))
            .order_by(Message.created_at)
        )
        history = _hydrate_history(list(history_result.scalars().all()))
        enabled_servers = await _load_enabled_mcp_servers(session)
        # Never let a programmatically-driven turn (scheduler / MCP post_message)
        # re-expose Precursor's own MCP server to itself — that would let a
        # post_message-triggered turn recursively call post_message.
        enabled_servers = [s for s in enabled_servers if s != "precursor"]
        model = await resolve_llm_model(session)
        max_tool_rounds = await resolve_max_tool_rounds(session)
        max_input_tokens = await resolve_llm_max_input_tokens(session)
        max_tool_result_tokens = await resolve_llm_max_tool_result_tokens(session)

    async with AsyncExitStack() as stack:
        sessions: dict[str, Any] = {}
        tool_to_server: dict[str, tuple[str, str]] = {}
        aggregated_tools: list[MCPToolDef] = []

        for server_name in enabled_servers:
            try:
                mcp_session, tools = await stack.enter_async_context(
                    manager.open_session(server_name, settings=settings)
                )
            except Exception as exc:
                logger.warning("Scheduled run: MCP server %s unavailable: %s", server_name, exc)
                continue
            sessions[server_name] = mcp_session
            for t in tools:
                tool_to_server[t.qualified_name] = (server_name, t.name)
            aggregated_tools.extend(tools)

        provider_tools = _mcp_tools_to_provider(aggregated_tools)
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
            *history,
        ]

        for _round in range(max_tool_rounds):
            text_chunks: list[str] = []
            tool_calls: list[Any] = []
            round_usage: UsageEvent | None = None

            async for event in provider.stream_chat_with_tools(
                model=model,
                messages=trim_messages(
                    messages,
                    max_input_tokens=max_input_tokens,
                    per_message_max_tokens=max_tool_result_tokens,
                ),
                tools=provider_tools,
            ):
                if isinstance(event, TextDeltaEvent):
                    text_chunks.append(event.content)
                elif isinstance(event, ToolCallsEvent):
                    tool_calls = event.calls
                elif isinstance(event, UsageEvent):
                    round_usage = event
                elif isinstance(event, TurnDoneEvent):
                    pass

            assistant_text = "".join(text_chunks)

            if not tool_calls:
                async with SessionLocal() as ws:
                    ws.add(
                        Message(
                            topic_id=topic_id,
                            role=MessageRole.ASSISTANT,
                            content=assistant_text,
                            prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                            completion_tokens=round_usage.completion_tokens
                            if round_usage
                            else None,
                        )
                    )
                    await ws.commit()
                await publish_message_changed(topic_id)
                return

            openai_tool_calls = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in tool_calls
            ]
            async with SessionLocal() as ws:
                ws.add(
                    Message(
                        topic_id=topic_id,
                        role=MessageRole.ASSISTANT,
                        content=assistant_text,
                        tool_calls=json.dumps(openai_tool_calls),
                        prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                        completion_tokens=round_usage.completion_tokens if round_usage else None,
                    )
                )
                await ws.commit()
            await publish_message_changed(topic_id)

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=assistant_text,
                    tool_calls=openai_tool_calls,
                )
            )

            for call in tool_calls:
                server_lookup = tool_to_server.get(call.name)
                is_error = False
                if server_lookup is None:
                    result_text = f"Unknown tool '{call.name}'. No MCP server exposes it."
                    is_error = True
                else:
                    server_name, raw_name = server_lookup
                    try:
                        args = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError as exc:
                        args = None
                        result_text = f"Invalid JSON arguments: {exc}"
                        is_error = True
                    if args is not None:
                        try:
                            result = await sessions[server_name].call_tool(raw_name, args)
                            result_text = _format_tool_result(result)
                            is_error = bool(getattr(result, "isError", False))
                        except Exception as exc:
                            logger.warning(
                                "Scheduled run: MCP call %s failed: %s",
                                call.name,
                                exc,
                            )
                            result_text = f"Tool call failed: {exc}"
                            is_error = True

                async with SessionLocal() as ws:
                    ws.add(
                        Message(
                            topic_id=topic_id,
                            role=MessageRole.TOOL,
                            content=result_text,
                            tool_calls=json.dumps(
                                {
                                    "tool_call_id": call.id,
                                    "name": call.name,
                                    "arguments": call.arguments,
                                    "is_error": is_error,
                                }
                            ),
                        )
                    )
                    await ws.commit()
                await publish_message_changed(topic_id)

                messages.append(
                    ChatMessage(
                        role="tool",
                        content=result_text,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

        # Exhausted the tool-round budget without a final answer.
        async with SessionLocal() as ws:
            ws.add(
                Message(
                    topic_id=topic_id,
                    role=MessageRole.ASSISTANT,
                    content=f"(Stopped after {max_tool_rounds} tool rounds.)",
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
