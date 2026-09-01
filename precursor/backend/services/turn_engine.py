"""The conversational turn engine — system context, history hydration, and the
MCP tool loop — shared by the SSE chat routers and the background scheduler.

The engine is container-agnostic: a turn belongs to exactly one of a topic or a
chat (see :data:`ContainerKind`). :func:`run_tool_loop` drives the provider +
MCP tool loop and yields semantic :class:`TurnEvent`s; each consumer applies its
own persistence/emission policy on top. :func:`run_message_stream` is the SSE
consumer (rich persistence + Server-Sent Events); the scheduler
(``services/turn.py``) is the plain consumer. Sharing the loop keeps the two
paths from diverging.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.db import SessionLocal
from precursor.backend.models import Chat, Message, MessageRole, Topic
from precursor.backend.services import memories as memory_service
from precursor.backend.services import skills as skills_service
from precursor.backend.services.attachment_extraction import (
    attachments_to_image_urls,
    attachments_to_text_context,
    is_image_attachment,
)
from precursor.backend.services.collections import resolve_topic_github_repo
from precursor.backend.services.context_budget import trim_messages
from precursor.backend.services.events import (
    publish_message_changed,
    publish_message_changed_chat,
    publish_stream_ended,
    publish_stream_ended_chat,
    publish_stream_started,
    publish_stream_started_chat,
)
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import GitHubClient
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    TextDeltaEvent,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.mcp.client import (
    AUTH_PAUSE_TIMEOUT_SECONDS,
    MCPToolAuthRequired,
    MCPToolDef,
    get_mcp_client_manager,
)
from precursor.backend.services.mcp.workspace_links import link_from_result
from precursor.backend.services.meeting_analysis import live_chat_grounding
from precursor.backend.services.roles import resolve_role_prompt
from precursor.backend.services.suggestions import SUGGESTIONS_INSTRUCTION, split_suggestions
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

# Container abstraction: a message belongs to exactly one of a topic or a chat.
ContainerKind = Literal["topic", "chat"]


# -- System context --------------------------------------------------------


async def build_system_context(session: AsyncSession, topic: Topic) -> str:
    """Compose system prompt: role persona + long-term memory + topic context + GitHub issue + labels (newest comments first)."""
    parts: list[str] = [
        "You are Precursor, a focused assistant for the topic below. "
        "Use the linked GitHub issue context (when present) as authoritative; "
        "newer updates and comments outweigh older ones. "
        "When tools are available, prefer fresh tool calls over stale context for "
        "anything that may have changed.",
    ]

    role_prompt = await resolve_role_prompt(session, topic.role_id)
    if role_prompt:
        parts.append(f"Active assistant role — adopt this persona for every reply:\n{role_prompt}")

    memory_block = await memory_service.build_memory_prompt(session)
    if memory_block:
        parts.append(memory_block)

    parts.append(f"Topic title: {topic.title}")
    if topic.description:
        parts.append(f"Topic description: {topic.description}")

    repo = await resolve_topic_github_repo(session, topic)
    token = await resolve_github_token(session)
    if repo and topic.github_issue_number and token:
        try:
            gh = GitHubClient(token=token)
            issue = await gh.get_issue(repo, topic.github_issue_number)
            comments = await gh.list_issue_comments(repo, topic.github_issue_number)
            await gh.aclose()
        except Exception as exc:  # pragma: no cover - network failure is non-fatal
            parts.append(f"(GitHub context unavailable: {exc})")
        else:
            label_names = [
                label["name"] if isinstance(label, dict) else label
                for label in issue.get("labels", [])
            ]
            labels = ", ".join(label_names) or "(none)"
            parts.append(
                f"Linked issue: {repo}#{topic.github_issue_number} — {issue.get('title', '')}"
            )
            parts.append(f"Issue labels: {labels}")
            if issue.get("body"):
                parts.append(f"Issue body:\n{issue['body']}")
            for c in list(reversed(comments))[:10]:
                parts.append(f"Comment by {c['user']} @ {c['updated_at']}:\n{c['body']}")
    parts.append(SUGGESTIONS_INSTRUCTION)
    return "\n\n".join(parts)


async def build_chat_system_context(session: AsyncSession, chat: Chat) -> str:
    """Compose system prompt for a chat: long-term memory + chat title/description.

    Chats are flat, GitHub-free sessions, so this is a lighter version of
    ``build_system_context`` without any issue/label context.
    """
    parts: list[str] = [
        "You are Precursor, a focused assistant. Answer the user's questions "
        "for the chat session below. When tools are available, prefer fresh "
        "tool calls over stale context for anything that may have changed.",
    ]

    role_prompt = await resolve_role_prompt(session, chat.role_id)
    if role_prompt:
        parts.append(f"Active assistant role — adopt this persona for every reply:\n{role_prompt}")

    memory_block = await memory_service.build_memory_prompt(session)
    if memory_block:
        parts.append(memory_block)

    parts.append(f"Chat title: {chat.title}")
    # In system-prompt mode the description is enforced per user turn (see
    # apply_chat_system_prompt), so we omit the soft context line here to avoid
    # duplicating it. In context mode (default) it rides along as standing
    # discussion-level context.
    if chat.description and not chat.description_as_system_prompt:
        description = await skills_service.expand_references(session, chat.description)
        parts.append(f"Chat description: {description}")

    # When this chat is attached to a live meeting session, fold in the current
    # meeting grounding (transcript/insights/notes/…), rebuilt every turn.
    grounding = await live_chat_grounding(session, chat.id)
    if grounding:
        parts.append(grounding)

    parts.append(SUGGESTIONS_INSTRUCTION)
    return "\n\n".join(parts)


def apply_chat_system_prompt(
    chat: Chat,
    history: list[ChatMessage],
    *,
    description: str | None = None,
) -> list[ChatMessage]:
    """Enforce a chat's description as a system instruction on every user turn.

    When ``description_as_system_prompt`` is set and the description is non-empty,
    prepend it to each user message sent to the LLM so the instruction is
    reasserted every turn rather than injected once. Empty description is a
    no-op. Returns a new list; the persisted messages are untouched.

    ``description`` overrides ``chat.description`` — callers pass the
    skill-expanded text (resolved with their DB session) so a description may
    trigger a skill by writing ``/skill-name``.
    """
    raw = chat.description if description is None else description
    description = (raw or "").strip()
    if not (chat.description_as_system_prompt and description):
        return history

    instruction = f"System instruction (must be followed at all times):\n{description}"
    out: list[ChatMessage] = []
    for m in history:
        if m.role == "user":
            prefixed = f"{instruction}\n\n{m.content}" if m.content else instruction
            out.append(ChatMessage(role="user", content=prefixed, image_urls=m.image_urls))
        else:
            out.append(m)
    return out


# -- History hydration -----------------------------------------------------


def hydrate_history(rows: list[Message]) -> list[ChatMessage]:
    """Turn persisted Messages back into ChatMessages, preserving tool calls.

    Drops orphan assistant-with-tool_calls turns (and any partial tool
    results) whose tool_call ids are not all answered. Anthropic-backed
    models reject such transcripts with a 400; OpenAI tolerates them but
    we want consistent behaviour.
    """
    out: list[ChatMessage] = []
    i = 0
    while i < len(rows):
        m = rows[i]
        role = m.role.value
        if role == "assistant" and m.tool_calls:
            try:
                calls = json.loads(m.tool_calls)
            except (TypeError, ValueError):
                calls = None
            expected_ids: set[str] = set()
            if isinstance(calls, list):
                for c in calls:
                    cid = c.get("id") if isinstance(c, dict) else None
                    if cid:
                        expected_ids.add(cid)
            # Collect contiguous tool rows that follow.
            tool_msgs: list[ChatMessage] = []
            seen_ids: set[str] = set()
            j = i + 1
            while j < len(rows) and rows[j].role.value == "tool":
                tm = rows[j]
                try:
                    meta = json.loads(tm.tool_calls) if tm.tool_calls else {}
                except (TypeError, ValueError):
                    meta = {}
                tcid = meta.get("tool_call_id") if isinstance(meta, dict) else None
                if tcid:
                    seen_ids.add(tcid)
                tool_msgs.append(
                    ChatMessage(
                        role="tool",
                        content=tm.content,
                        tool_call_id=tcid,
                        name=meta.get("name") if isinstance(meta, dict) else None,
                    )
                )
                j += 1
            if expected_ids and expected_ids.issubset(seen_ids):
                out.append(ChatMessage(role="assistant", content=m.content, tool_calls=calls))
                out.extend(tool_msgs)
            else:
                logger.info(
                    "Dropping orphan tool-call turn (expected %s, got %s)",
                    expected_ids,
                    seen_ids,
                )
            i = j
            continue
        if role == "tool":
            # Orphan tool message with no preceding assistant tool_calls — skip.
            i += 1
            continue
        if role == "system":
            # Persisted SYSTEM rows are UI-only notices (e.g. the "Run now
            # accepted" confirmation). The real system prompt is built fresh
            # each turn, so never feed these to the model.
            i += 1
            continue
        content = m.content
        image_urls: list[str] = []
        if role == "user" and m.attachments:
            user_attachments = list(m.attachments)
            image_urls = attachments_to_image_urls(
                [att for att in user_attachments if is_image_attachment(att)]
            )
            non_image_context = attachments_to_text_context(
                [att for att in user_attachments if not is_image_attachment(att)]
            )
            if non_image_context:
                content = f"{content}\n\n{non_image_context}" if content else non_image_context
        out.append(ChatMessage(role=role, content=content, image_urls=image_urls))
        i += 1
    return out


# -- MCP helpers -----------------------------------------------------------


async def load_enabled_mcp_servers(session: AsyncSession) -> list[str]:
    from precursor.backend.models import AppSetting

    row = await session.get(AppSetting, "mcp_enabled")
    if row is None:
        return []
    try:
        data = json.loads(row.value)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [name for name, enabled in data.items() if enabled]


def mcp_tools_to_provider(tools: list[MCPToolDef]) -> list[ToolDef]:
    return [
        ToolDef(
            name=t.qualified_name,
            description=t.description,
            parameters=t.input_schema,
        )
        for t in tools
    ]


def format_tool_result(payload: Any) -> str:
    """Stringify an MCP tool result for the LLM + UI."""
    # mcp.types.CallToolResult exposes .content as a list of content blocks.
    blocks: list[str] = []
    content = getattr(payload, "content", None)
    if content is None:
        return json.dumps(payload, default=str)
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            blocks.append(text)
        else:
            blocks.append(json.dumps(getattr(block, "model_dump", lambda: {})(), default=str))
    if blocks:
        return "\n\n".join(blocks)
    # Some MCP servers (e.g. the hosted WorkIQ endpoint) return no text content
    # blocks and put the payload in ``structuredContent`` instead. Falling back
    # to it keeps reads from looking empty to the model.
    structured = getattr(payload, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, default=str)
    return "(empty result)"


# -- Container persistence helpers -----------------------------------------


def container_message_kwargs(kind: ContainerKind, container_id: int) -> dict[str, int]:
    """Return the FK kwargs to attach a Message to its container."""
    return {"topic_id": container_id} if kind == "topic" else {"chat_id": container_id}


async def publish_container_changed(kind: ContainerKind, container_id: int) -> None:
    if kind == "topic":
        await publish_message_changed(container_id)
    else:
        await publish_message_changed_chat(container_id)


async def _persist_system_message(
    container_id: int, content: str, *, kind: ContainerKind = "topic"
) -> None:
    """Save a system-role message in a fresh session.

    Used for stream errors so they stay in the transcript instead of vanishing
    when the client reloads the persisted history after the stream ends. Uses a
    separate session because the request-scoped one may be closed by the time
    the generator reaches an error.
    """
    async with SessionLocal() as ws:
        ws.add(
            Message(
                role=MessageRole.SYSTEM,
                content=content,
                **container_message_kwargs(kind, container_id),
            )
        )
        await ws.commit()
    await publish_container_changed(kind, container_id)


async def prepare_retry_turn(
    session: AsyncSession,
    *,
    kind: ContainerKind,
    container_id: int,
    message_id: int,
) -> Message:
    """Rewind a container to just after ``message_id`` so that turn can re-run.

    A retry replays an existing user prompt whose turn failed, so nothing new is
    persisted: the original message (and its attachments) is reused and every
    row recorded after it — a partial answer, its tool rows, the `Error: …`
    notice — is deleted. Message ids are monotonic, so "after" is simply a
    greater id.

    Raises ``HTTPException`` when the id doesn't name a user turn of this
    container.
    """
    fk = Message.topic_id if kind == "topic" else Message.chat_id
    result = await session.execute(
        select(Message)
        .where(Message.id == message_id, fk == container_id)
        .options(selectinload(Message.attachments))
    )
    user_msg = result.scalar_one_or_none()
    if user_msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    if user_msg.role != MessageRole.USER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a user turn can be retried")

    await session.execute(delete(Message).where(fk == container_id, Message.id > message_id))
    await session.commit()
    await publish_container_changed(kind, container_id)
    return user_msg


# -- The tool loop ---------------------------------------------------------


@dataclass(slots=True)
class AssistantTextDelta:
    """A streamed chunk of assistant text."""

    content: str


@dataclass(slots=True)
class AssistantFinalTurn:
    """The model finished with a plain-text answer (no tool calls)."""

    text: str
    usage: UsageEvent | None


@dataclass(slots=True)
class AssistantToolCallsTurn:
    """The model requested one or more tool calls."""

    text: str
    tool_calls: list[Any]
    openai_tool_calls: list[dict[str, Any]]
    usage: UsageEvent | None


@dataclass(slots=True)
class ToolResultTurn:
    """A single tool call was executed."""

    call: Any
    result_text: str
    is_error: bool
    # ``{"slug", "path"}`` when the tool touched a workspace file, so the UI can
    # offer a link to it without parsing ``result_text`` — which for a read
    # embeds the whole file. None for every other tool.
    link: dict[str, str] | None = None


@dataclass(slots=True)
class ToolAuthRequired:
    """A tool the model called needs an interactive sign-in before it can run.

    Yielded at *call* time rather than turn-start time: the catalogue is
    advertised from the stored one even while a credential is stale, so an
    unrelated question is never held hostage by one expired token. The prompt is
    also strictly better than the old blanket one, because it can name the tool
    that actually needs the sign-in.
    """

    server: str
    message: str
    tool: str


@dataclass(slots=True)
class RoundCapReached:
    """The tool-round budget was exhausted without a final answer."""

    max_tool_rounds: int


TurnEvent = (
    AssistantTextDelta
    | AssistantFinalTurn
    | AssistantToolCallsTurn
    | ToolResultTurn
    | ToolAuthRequired
    | RoundCapReached
)


async def run_tool_loop(
    *,
    active: Any,
    provider: Any,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    history: list[ChatMessage],
    max_tool_rounds: int,
    max_input_tokens: int,
    max_tool_result_tokens: int,
    auth_wait_timeout: float = AUTH_PAUSE_TIMEOUT_SECONDS,
) -> AsyncIterator[TurnEvent]:
    """Drive the provider + MCP tool loop, yielding semantic turn events.

    Container-agnostic and side-effect-free apart from executing tool calls
    against ``active``: it never persists messages or emits transport events, so
    the SSE and scheduler consumers can apply their own persistence policy while
    sharing this control flow. ``active`` is an acquired MCP session handle
    (exposes ``tools``, ``tool_to_server`` and ``call_tool``).

    ``auth_wait_timeout`` is how long a call may pause for an interactive
    sign-in. Pass ``0`` for unattended runs (the scheduler), where nobody is
    there to complete one: the prompt is still surfaced, but the call fails fast
    with a tool error instead of parking the run for minutes.
    """
    tool_to_server = active.tool_to_server  # qualified -> (server, raw_name)
    provider_tools = mcp_tools_to_provider(active.tools)

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
            reasoning_effort=reasoning_effort,
        ):
            if isinstance(event, TextDeltaEvent):
                text_chunks.append(event.content)
                yield AssistantTextDelta(event.content)
            elif isinstance(event, ToolCallsEvent):
                tool_calls = event.calls
            elif isinstance(event, UsageEvent):
                round_usage = event
            elif isinstance(event, TurnDoneEvent):
                pass

        assistant_text = "".join(text_chunks)

        if not tool_calls:
            yield AssistantFinalTurn(assistant_text, round_usage)
            return

        openai_tool_calls = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in tool_calls
        ]
        yield AssistantToolCallsTurn(assistant_text, tool_calls, openai_tool_calls, round_usage)

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
            link: dict[str, str] | None = None
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
                    # One retry, and only for a sign-in: the catalogue was
                    # advertised from the stored one, so this is the first moment
                    # we know the credential is actually needed.
                    prompted = False
                    while True:
                        try:
                            result = await active.call_tool(server_name, raw_name, args)
                        except MCPToolAuthRequired as exc:
                            if not prompted:
                                prompted = True
                                yield ToolAuthRequired(
                                    server=exc.server, message=exc.message, tool=call.name
                                )
                                if auth_wait_timeout > 0:
                                    await active.manager.wait_for_auth(timeout=auth_wait_timeout)
                                    continue
                            # Never let the model answer as though the tool had
                            # run: report the failure so it says what it couldn't
                            # reach instead of inventing the result.
                            result_text = (
                                f"Tool '{call.name}' is unavailable: {exc.server} needs an "
                                f"interactive sign-in ({exc.message}). Sign in from the banner "
                                "and ask again; do not guess the answer."
                            )
                            is_error = True
                            break
                        except Exception as exc:
                            logger.warning(
                                "MCP call %s(%s) failed: %s",
                                call.name,
                                call.arguments,
                                exc,
                            )
                            result_text = f"Tool call failed: {exc}"
                            is_error = True
                            break
                        else:
                            result_text = format_tool_result(result)
                            is_error = bool(getattr(result, "isError", False))
                            if not is_error:
                                link = link_from_result(result)
                            break

            yield ToolResultTurn(call, result_text, is_error, link)

            messages.append(
                ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=call.id,
                    name=call.name,
                )
            )

    yield RoundCapReached(max_tool_rounds)


# -- SSE consumer ----------------------------------------------------------


async def run_message_stream(
    *,
    kind: ContainerKind,
    container_id: int,
    system_prompt: str,
    history: list[ChatMessage],
    user_echo: dict[str, Any],
    model: str,
    reasoning_effort: str,
    max_tool_rounds: int,
    max_input_tokens: int,
    max_tool_result_tokens: int,
    provider: Any,
    github_token: str,
    enabled_servers: list[str],
) -> AsyncIterator[dict[str, str]]:
    """Container-agnostic SSE generator shared by topic and chat streaming.

    Persists assistant/tool turns against the right container (topic or chat),
    runs the shared MCP tool loop, and yields SSE events. Errors are persisted
    as system messages so they survive a client reload.
    """
    manager = get_mcp_client_manager()

    yield {
        "event": "user_message",
        "data": json.dumps(user_echo),
    }

    # No pre-LLM auth gate: the turn starts now. The catalogue of a server whose
    # credential has lapsed is still advertised (from the stored one), so the
    # model can't quietly answer from memory instead of calling the tool — and a
    # question that never touches that server is no longer held hostage by it.
    # The sign-in is asked for at call time, by the dispatch loop, naming the
    # tool that needs it.
    async with manager.acquired(
        enabled_servers, github_token=github_token, advertise_cached=True
    ) as active:
        # Warm MCP sessions for the enabled servers (reused across turns). Each
        # contributes tools we aggregate for the LLM; failures are surfaced but
        # don't abort the turn.
        for server_name, err in active.unavailable:
            logger.warning("MCP server %s unavailable: %s", server_name, err)
            if server_name in active.advertised_from_cache:
                # Its tools are on offer and it reconnects on first use, so
                # saying "unavailable" here would be both noisy and wrong.
                continue
            yield {
                "event": "system",
                "data": json.dumps({"message": f"MCP server '{server_name}' unavailable: {err}"}),
            }

        try:
            turn_started = time.monotonic()
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
                    yield {
                        "event": "delta",
                        "data": json.dumps({"content": ev.content}),
                    }

                elif isinstance(ev, AssistantFinalTurn):
                    # Final assistant turn — split off any suggested follow-ups,
                    # persist the clean text, and surface the chips separately.
                    round_usage = ev.usage
                    assistant_text, suggestions = split_suggestions(ev.text)
                    elapsed_ms = int((time.monotonic() - turn_started) * 1000)
                    async with SessionLocal() as ws:
                        assistant = Message(
                            role=MessageRole.ASSISTANT,
                            content=assistant_text,
                            suggestions=json.dumps(suggestions) if suggestions else None,
                            prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                            completion_tokens=round_usage.completion_tokens
                            if round_usage
                            else None,
                            model=model,
                            elapsed_ms=elapsed_ms,
                            **container_message_kwargs(kind, container_id),
                        )
                        ws.add(assistant)
                        await ws.commit()
                        await ws.refresh(assistant)
                        assistant_id = assistant.id
                        await publish_container_changed(kind, container_id)
                        if round_usage is not None:
                            async with SessionLocal() as us:
                                await record_usage(
                                    us,
                                    prompt_tokens=round_usage.prompt_tokens,
                                    completion_tokens=round_usage.completion_tokens,
                                    total_tokens=round_usage.total_tokens,
                                    source="chat",
                                    model=model,
                                    **container_message_kwargs(kind, container_id),
                                )
                                await us.commit()
                            yield {
                                "event": "usage",
                                "data": json.dumps(
                                    {
                                        "message_id": assistant.id,
                                        "prompt_tokens": round_usage.prompt_tokens,
                                        "completion_tokens": round_usage.completion_tokens,
                                        "total_tokens": round_usage.total_tokens,
                                    }
                                ),
                            }
                        yield {
                            "event": "done",
                            "data": json.dumps(
                                {
                                    "id": assistant.id,
                                    "content": assistant_text,
                                    "model": model,
                                    "elapsed_ms": elapsed_ms,
                                }
                            ),
                        }
                    if suggestions:
                        yield {
                            "event": "suggestions",
                            "data": json.dumps({"message_id": assistant_id, "items": suggestions}),
                        }
                    return

                elif isinstance(ev, AssistantToolCallsTurn):
                    # Persist assistant-with-tool-calls turn.
                    round_usage = ev.usage
                    async with SessionLocal() as ws:
                        assistant = Message(
                            role=MessageRole.ASSISTANT,
                            content=ev.text,
                            tool_calls=json.dumps(ev.openai_tool_calls),
                            prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                            completion_tokens=round_usage.completion_tokens
                            if round_usage
                            else None,
                            **container_message_kwargs(kind, container_id),
                        )
                        ws.add(assistant)
                        await ws.commit()
                        await ws.refresh(assistant)
                        assistant_id = assistant.id
                    await publish_container_changed(kind, container_id)

                    if round_usage is not None:
                        async with SessionLocal() as us:
                            await record_usage(
                                us,
                                prompt_tokens=round_usage.prompt_tokens,
                                completion_tokens=round_usage.completion_tokens,
                                total_tokens=round_usage.total_tokens,
                                source="chat",
                                model=model,
                                **container_message_kwargs(kind, container_id),
                            )
                            await us.commit()
                        yield {
                            "event": "usage",
                            "data": json.dumps(
                                {
                                    "message_id": assistant_id,
                                    "prompt_tokens": round_usage.prompt_tokens,
                                    "completion_tokens": round_usage.completion_tokens,
                                    "total_tokens": round_usage.total_tokens,
                                }
                            ),
                        }

                    yield {
                        "event": "tool_calls",
                        "data": json.dumps(
                            {
                                "assistant_id": assistant_id,
                                "calls": [
                                    {
                                        "id": c.id,
                                        "name": c.name,
                                        "arguments": c.arguments,
                                    }
                                    for c in ev.tool_calls
                                ],
                            }
                        ),
                    }

                elif isinstance(ev, ToolResultTurn):
                    call = ev.call
                    tool_meta: dict[str, Any] = {
                        "tool_call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "is_error": ev.is_error,
                    }
                    if ev.link:
                        tool_meta["link"] = ev.link
                    async with SessionLocal() as ws:
                        tool_msg = Message(
                            role=MessageRole.TOOL,
                            content=ev.result_text,
                            tool_calls=json.dumps(tool_meta),
                            **container_message_kwargs(kind, container_id),
                        )
                        ws.add(tool_msg)
                        await ws.commit()
                        await ws.refresh(tool_msg)
                        tool_msg_id = tool_msg.id
                    await publish_container_changed(kind, container_id)

                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {
                                "message_id": tool_msg_id,
                                "tool_call_id": call.id,
                                "name": call.name,
                                "arguments": call.arguments,
                                "content": ev.result_text,
                                "is_error": ev.is_error,
                                "link": ev.link,
                            }
                        ),
                    }

                elif isinstance(ev, ToolAuthRequired):
                    # The model reached for a tool whose credential has lapsed.
                    # This is the moment the sign-in is genuinely required, so
                    # prompt now — naming the tool — rather than at turn start.
                    yield {
                        "event": "mcp_auth_required",
                        "data": json.dumps(
                            {
                                "server": ev.server,
                                "message": ev.message,
                                "tool": ev.tool,
                            }
                        ),
                    }

                elif isinstance(ev, RoundCapReached):
                    # Hit the iteration cap.
                    cap_msg = f"Stopped after {ev.max_tool_rounds} tool rounds."
                    await _persist_system_message(container_id, f"Error: {cap_msg}", kind=kind)
                    yield {
                        "event": "error",
                        "data": json.dumps({"message": cap_msg}),
                    }
        except LLMError as exc:
            # Provider rejected the request for a reason worth showing the
            # user (too many tools, bad credentials, …) — surface it cleanly
            # without a crash-style traceback.
            logger.warning("Chat stream rejected by provider: %s", exc)
            await _persist_system_message(container_id, f"Error: {exc}", kind=kind)
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
        except Exception as exc:
            logger.exception("Chat stream failed")
            await _persist_system_message(container_id, f"Error: {exc}", kind=kind)
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}


def lifecycle_stream(
    kind: ContainerKind,
    container_id: int,
    inner: AsyncIterator[dict[str, str]],
) -> AsyncIterator[dict[str, str]]:
    """Wrap an event stream with stream.started / stream.ended publishing."""

    async def gen() -> AsyncIterator[dict[str, str]]:
        if kind == "topic":
            await publish_stream_started(container_id)
        else:
            await publish_stream_started_chat(container_id)
        try:
            async for evt in inner:
                yield evt
        finally:
            if kind == "topic":
                await publish_stream_ended(container_id)
            else:
                await publish_stream_ended_chat(container_id)

    return gen()
