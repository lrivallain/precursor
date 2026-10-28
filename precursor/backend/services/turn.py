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
from dataclasses import dataclass

import anyio

from precursor.backend.db import SessionLocal
from precursor.backend.models import Chat, Message, MessageRole, Topic
from precursor.backend.services import approvals as approval_service
from precursor.backend.services import skills as skills_service
from precursor.backend.services.conversation_turn import (
    clear_container_messages,
    persist_user_message,
    resolve_turn_settings,
    snapshot_history,
)
from precursor.backend.services.events import (
    publish_approval_changed,
    publish_stream_ended,
    publish_stream_ended_chat,
    publish_stream_started,
    publish_stream_started_chat,
)
from precursor.backend.services.mcp.client import get_mcp_client_manager
from precursor.backend.services.turn_engine import (
    ApprovalsRequested,
    AssistantFinalTurn,
    AssistantTextDelta,
    AssistantToolCallsTurn,
    ContainerKind,
    RoundCapReached,
    ToolAuthRequired,
    ToolResultTurn,
    apply_chat_system_prompt,
    build_chat_system_context,
    build_system_context,
    container_message_kwargs,
    persist_final_turn,
    persist_tool_calls_turn,
    persist_tool_result,
    publish_container_changed,
    run_tool_loop,
)
from precursor.backend.services.unattended_approvals import announce_parked
from precursor.backend.services.workspaces import WORKSPACE_READ_TOOLS, WORKSPACE_WRITE_TOOLS

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnResult:
    """What a headless turn left behind, for callers that act on the reply."""

    user_message_id: int | None = None
    reply: str | None = None
    awaiting_approval: bool = False


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
    await run_container_turn(
        "topic", topic_id, prompt, clear_context=clear_context, llm_prompt=llm_prompt
    )


async def run_chat_turn(chat_id: int, prompt: str, *, llm_prompt: str | None = None) -> TurnResult:
    """The same headless turn for a chat (a step's or a plan's discussion)."""
    return await run_container_turn("chat", chat_id, prompt, llm_prompt=llm_prompt)


async def run_container_turn(
    kind: ContainerKind,
    container_id: int,
    prompt: str,
    *,
    clear_context: bool = False,
    llm_prompt: str | None = None,
) -> TurnResult:
    if kind == "topic":
        await publish_stream_started(container_id)
    else:
        await publish_stream_started_chat(container_id)
    try:
        return await _run(
            kind, container_id, prompt, clear_context=clear_context, llm_prompt=llm_prompt
        )
    finally:
        if kind == "topic":
            await publish_stream_ended(container_id)
        else:
            await publish_stream_ended_chat(container_id)


async def _run(
    kind: ContainerKind,
    container_id: int,
    prompt: str,
    *,
    clear_context: bool = False,
    llm_prompt: str | None = None,
) -> TurnResult:
    manager = get_mcp_client_manager()
    result = TurnResult()
    topic_id = container_id if kind == "topic" else None

    async with SessionLocal() as session:
        container: Topic | Chat | None
        if kind == "topic":
            container = await session.get(Topic, container_id)
        else:
            container = await session.get(Chat, container_id)
        if container is None:
            logger.warning("Headless run for missing %s %s", kind, container_id)
            return result

        # Optionally wipe prior turns so each run is independent of history.
        if clear_context:
            await clear_container_messages(session, kind, container_id)

        # A chat left parked on an approval is moved on from: settle its open
        # approvals so their "not run" rows land before the new prompt.
        if kind == "chat" and await approval_service.abandon_open(session, kind, container_id):
            await publish_approval_changed(chat_id=container_id)

        # Persist the prompt as the user turn so the transcript and the unread
        # badge behave like a normal conversation.
        user_msg = await persist_user_message(session, kind, container_id, prompt)
        result.user_message_id = user_msg.id

        if isinstance(container, Chat):
            system_prompt = await build_chat_system_context(session, container)
            history = await snapshot_history(
                session, kind, container_id, prompt_override=llm_prompt
            )
            history = apply_chat_system_prompt(
                container,
                history,
                description=await skills_service.expand_references(
                    session, container.description or ""
                ),
            )
            settings = await resolve_turn_settings(session, chat_id=container_id)
        else:
            system_prompt = await build_system_context(session, container)
            history = await snapshot_history(
                session, kind, container_id, prompt_override=llm_prompt
            )
            settings = await resolve_turn_settings(session, topic_id=container_id)

    async with manager.acquired(
        settings.enabled_servers, github_token=settings.github_token, advertise_cached=True
    ) as active:
        # Never let a programmatically-driven turn (scheduler / MCP post_message)
        # re-expose Precursor's own MCP server to itself — that would let a
        # post_message-triggered turn recursively call post_message. Only the
        # workspace file tools stay, as they did when they were a separate server.
        active.restrict_server_tools("precursor", WORKSPACE_READ_TOOLS | WORKSPACE_WRITE_TOOLS)
        for server_name, err in active.unavailable:
            logger.warning("Headless run: MCP server %s unavailable: %s", server_name, err)

        turn_started = time.monotonic()
        last_assistant_id: int | None = None
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
            gate=settings.gate,
        ):
            if isinstance(ev, AssistantTextDelta):
                # Headless runs don't stream deltas; they persist whole turns.
                continue

            if isinstance(ev, ToolAuthRequired):
                from precursor.backend.services.events import publish_mcp_auth_required

                logger.warning("Headless run: %s needs a sign-in for %s", ev.server, ev.tool)
                await publish_mcp_auth_required(ev.server, ev.message, topic_id=topic_id)
                continue

            # Stored exactly as a streamed turn stores them (clean text + chips,
            # model, elapsed time, and a usage-ledger row per metered round).
            if isinstance(ev, AssistantFinalTurn):
                elapsed_ms = int((time.monotonic() - turn_started) * 1000)
                answer = await persist_final_turn(
                    kind, container_id, ev, model=ev.model or settings.model, elapsed_ms=elapsed_ms
                )
                result.reply = answer.text
                return result

            if isinstance(ev, AssistantToolCallsTurn):
                last_assistant_id = await persist_tool_calls_turn(
                    kind, container_id, ev, model=ev.model or settings.model
                )

            elif isinstance(ev, ApprovalsRequested):
                # Nobody is watching, so park: the cards (and the sidebar badge)
                # wait for the user, who resumes the turn from the conversation.
                async with SessionLocal() as ws:
                    rows = await approval_service.record_pending(
                        ws, kind, container_id, last_assistant_id, ev.items
                    )
                    parked_ids = [r.id for r in rows]
                result.awaiting_approval = True
                if kind == "topic":
                    # Starts the auto-deny clock and notifies the windows.
                    await announce_parked(container_id, parked_ids)
                else:
                    await publish_approval_changed(chat_id=container_id)
                return result

            elif isinstance(ev, ToolResultTurn):
                await persist_tool_result(kind, container_id, ev)

            elif isinstance(ev, RoundCapReached):
                # Exhausted the tool-round budget without a final answer. This
                # stays the unattended turn's own assistant note; the stream
                # records an "Error: …" system row instead.
                async with SessionLocal() as ws:
                    ws.add(
                        Message(
                            role=MessageRole.ASSISTANT,
                            content=f"(Stopped after {ev.max_tool_rounds} tool rounds.)",
                            **container_message_kwargs(kind, container_id),
                        )
                    )
                    await ws.commit()
                await publish_container_changed(kind, container_id)
    return result


# Re-exported for the scheduler's timeout wrapper.
__all__ = ["TurnResult", "run_chat_turn", "run_topic_turn"]


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
