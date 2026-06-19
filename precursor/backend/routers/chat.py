"""Chat router — list messages, post a new turn, stream the assistant reply over SSE."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from precursor.backend.db import SessionLocal, get_session
from precursor.backend.models import Attachment, Chat, Memory, Message, MessageRole, Topic
from precursor.backend.schemas import ChatRequest, MessageRead, StoppedTurn
from precursor.backend.services.app_settings import (
    resolve_global_github_repo,
    resolve_llm_max_input_tokens,
    resolve_llm_max_tool_result_tokens,
    resolve_llm_model,
    resolve_max_tool_rounds,
)
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
from precursor.backend.services.llm import get_llm_provider
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    TextDeltaEvent,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.mcp.client import MCPToolDef, get_mcp_client_manager
from precursor.backend.services.roles import resolve_role_prompt
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topics/{topic_id}/messages", tags=["chat"])


@router.get("", response_model=list[MessageRead])
async def list_messages(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[Message]:
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    result = await session.execute(
        select(Message)
        .where(Message.topic_id == topic_id)
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at)
    )
    return list(result.scalars().all())


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(
    topic_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Wipe the chat transcript for a topic. Topic + GitHub link are kept."""
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    await session.execute(delete(Message).where(Message.topic_id == topic_id))
    await session.commit()
    await publish_message_changed(topic_id)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    topic_id: int,
    message_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard-delete a single message. Attachments cascade with the row."""
    msg = await session.get(Message, message_id)
    if msg is None or msg.topic_id != topic_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    await session.delete(msg)
    await session.commit()
    await publish_message_changed(topic_id)


@router.post("/stopped", response_model=MessageRead)
async def save_stopped_turn(
    topic_id: int,
    payload: StoppedTurn,
    session: AsyncSession = Depends(get_session),
) -> Message:
    """Persist the partial assistant reply when the user stops generation.

    The streaming endpoint only saves the final turn, which never runs once the
    client disconnects. This lets the client keep the text it already received
    instead of losing it on stop.
    """
    if await session.get(Topic, topic_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    msg = Message(
        topic_id=topic_id,
        role=MessageRole.ASSISTANT,
        content=payload.content,
    )
    session.add(msg)
    await session.commit()
    await publish_message_changed(topic_id)
    # Re-load with attachments eagerly so MessageRead serialization doesn't
    # trigger a lazy load outside the async context.
    result = await session.execute(
        select(Message).where(Message.id == msg.id).options(selectinload(Message.attachments))
    )
    return result.scalar_one()


async def _build_system_context(session: AsyncSession, topic: Topic) -> str:
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

    memories_result = await session.execute(select(Memory).order_by(Memory.kind, Memory.created_at))
    memories = list(memories_result.scalars().all())
    if memories:
        lines = [
            "Long-term user memory — treat as standing context for every turn:",
        ]
        for m in memories:
            lines.append(f"- [{m.kind.upper()}] {m.content.strip()}")
        parts.append("\n".join(lines))

    parts.append(f"Topic title: {topic.title}")
    if topic.description:
        parts.append(f"Topic description: {topic.description}")

    repo = topic.github_repo or await resolve_global_github_repo(session)
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
    return "\n\n".join(parts)


async def _build_chat_system_context(session: AsyncSession, chat: Chat) -> str:
    """Compose system prompt for a chat: long-term memory + chat title/description.

    Chats are flat, GitHub-free sessions, so this is a lighter version of
    ``_build_system_context`` without any issue/label context.
    """
    parts: list[str] = [
        "You are Precursor, a focused assistant. Answer the user's questions "
        "for the chat session below. When tools are available, prefer fresh "
        "tool calls over stale context for anything that may have changed.",
    ]

    role_prompt = await resolve_role_prompt(session, chat.role_id)
    if role_prompt:
        parts.append(f"Active assistant role — adopt this persona for every reply:\n{role_prompt}")

    memories_result = await session.execute(select(Memory).order_by(Memory.kind, Memory.created_at))
    memories = list(memories_result.scalars().all())
    if memories:
        lines = ["Long-term user memory — treat as standing context for every turn:"]
        for m in memories:
            lines.append(f"- [{m.kind.upper()}] {m.content.strip()}")
        parts.append("\n".join(lines))

    parts.append(f"Chat title: {chat.title}")
    if chat.description:
        parts.append(f"Chat description: {chat.description}")
    return "\n\n".join(parts)


def _attachments_to_image_urls(atts: list[Attachment]) -> list[str]:
    """Inline image attachments as ``data:`` URLs for vision-capable providers."""
    urls: list[str] = []
    for a in atts:
        b64 = base64.b64encode(a.data).decode("ascii")
        urls.append(f"data:{a.mime};base64,{b64}")
    return urls


def _hydrate_history(rows: list[Message]) -> list[ChatMessage]:
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
        image_urls = (
            _attachments_to_image_urls(list(m.attachments))
            if role == "user" and m.attachments
            else []
        )
        out.append(ChatMessage(role=role, content=m.content, image_urls=image_urls))
        i += 1
    return out


async def _load_enabled_mcp_servers(session: AsyncSession) -> list[str]:
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


def _mcp_tools_to_provider(
    tools: list[MCPToolDef],
) -> list[ToolDef]:
    return [
        ToolDef(
            name=t.qualified_name,
            description=t.description,
            parameters=t.input_schema,
        )
        for t in tools
    ]


def _format_tool_result(payload: Any) -> str:
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
    return "\n\n".join(blocks) if blocks else "(empty result)"


# Container abstraction: a message belongs to exactly one of a topic or a chat.
ContainerKind = Literal["topic", "chat"]


def _container_message_kwargs(kind: ContainerKind, container_id: int) -> dict[str, int]:
    """Return the FK kwargs to attach a Message to its container."""
    return {"topic_id": container_id} if kind == "topic" else {"chat_id": container_id}


async def _publish_container_changed(kind: ContainerKind, container_id: int) -> None:
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
                **_container_message_kwargs(kind, container_id),
            )
        )
        await ws.commit()
    await _publish_container_changed(kind, container_id)


async def _run_message_stream(
    *,
    kind: ContainerKind,
    container_id: int,
    system_prompt: str,
    history: list[ChatMessage],
    user_echo: dict[str, Any],
    model: str,
    max_tool_rounds: int,
    max_input_tokens: int,
    max_tool_result_tokens: int,
    provider: Any,
    github_token: str,
    enabled_servers: list[str],
) -> AsyncIterator[dict[str, str]]:
    """Container-agnostic SSE generator shared by topic and chat streaming.

    Persists assistant/tool turns against the right container (topic or chat),
    runs the MCP tool loop, and yields SSE events. Errors are persisted as
    system messages so they survive a client reload.
    """
    manager = get_mcp_client_manager()

    yield {
        "event": "user_message",
        "data": json.dumps(user_echo),
    }

    async with AsyncExitStack() as stack:
        # Open one live MCP session per enabled server. Each yields a
        # (session, tools) tuple; we aggregate all tools for the LLM.
        sessions: dict[str, Any] = {}
        tool_to_server: dict[str, tuple[str, str]] = {}  # qualified -> (server, raw_name)
        aggregated_tools: list[MCPToolDef] = []

        for server_name in enabled_servers:
            try:
                mcp_session, tools = await stack.enter_async_context(
                    manager.open_session(server_name, github_token=github_token)
                )
            except Exception as exc:
                logger.warning("MCP server %s unavailable: %s", server_name, exc)
                yield {
                    "event": "system",
                    "data": json.dumps(
                        {"message": f"MCP server '{server_name}' unavailable: {exc}"}
                    ),
                }
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

        try:
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
                        yield {
                            "event": "delta",
                            "data": json.dumps({"content": event.content}),
                        }
                    elif isinstance(event, ToolCallsEvent):
                        tool_calls = event.calls
                    elif isinstance(event, UsageEvent):
                        round_usage = event
                    elif isinstance(event, TurnDoneEvent):
                        pass

                assistant_text = "".join(text_chunks)

                if not tool_calls:
                    # Final assistant turn — persist and done.
                    async with SessionLocal() as ws:
                        assistant = Message(
                            role=MessageRole.ASSISTANT,
                            content=assistant_text,
                            prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                            completion_tokens=round_usage.completion_tokens
                            if round_usage
                            else None,
                            **_container_message_kwargs(kind, container_id),
                        )
                        ws.add(assistant)
                        await ws.commit()
                        await ws.refresh(assistant)
                        await _publish_container_changed(kind, container_id)
                        if round_usage is not None:
                            async with SessionLocal() as us:
                                await record_usage(
                                    us,
                                    prompt_tokens=round_usage.prompt_tokens,
                                    completion_tokens=round_usage.completion_tokens,
                                    total_tokens=round_usage.total_tokens,
                                    source="chat",
                                    model=model,
                                    **_container_message_kwargs(kind, container_id),
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
                            "data": json.dumps({"id": assistant.id, "content": assistant_text}),
                        }
                    return

                # Persist assistant-with-tool-calls turn.
                openai_tool_calls = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in tool_calls
                ]
                async with SessionLocal() as ws:
                    assistant = Message(
                        role=MessageRole.ASSISTANT,
                        content=assistant_text,
                        tool_calls=json.dumps(openai_tool_calls),
                        prompt_tokens=round_usage.prompt_tokens if round_usage else None,
                        completion_tokens=round_usage.completion_tokens if round_usage else None,
                        **_container_message_kwargs(kind, container_id),
                    )
                    ws.add(assistant)
                    await ws.commit()
                    await ws.refresh(assistant)
                    assistant_id = assistant.id
                await _publish_container_changed(kind, container_id)

                if round_usage is not None:
                    async with SessionLocal() as us:
                        await record_usage(
                            us,
                            prompt_tokens=round_usage.prompt_tokens,
                            completion_tokens=round_usage.completion_tokens,
                            total_tokens=round_usage.total_tokens,
                            source="chat",
                            model=model,
                            **_container_message_kwargs(kind, container_id),
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
                                for c in tool_calls
                            ],
                        }
                    ),
                }

                # Add assistant + tool messages to the in-memory transcript.
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=assistant_text,
                        tool_calls=openai_tool_calls,
                    )
                )

                # Execute each tool call against the right MCP session.
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
                                mcp_session = sessions[server_name]
                                result = await mcp_session.call_tool(raw_name, args)
                                result_text = _format_tool_result(result)
                                is_error = bool(getattr(result, "isError", False))
                            except Exception as exc:
                                logger.warning(
                                    "MCP call %s(%s) failed: %s",
                                    call.name,
                                    call.arguments,
                                    exc,
                                )
                                result_text = f"Tool call failed: {exc}"
                                is_error = True

                    async with SessionLocal() as ws:
                        tool_msg = Message(
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
                            **_container_message_kwargs(kind, container_id),
                        )
                        ws.add(tool_msg)
                        await ws.commit()
                        await ws.refresh(tool_msg)
                        tool_msg_id = tool_msg.id
                    await _publish_container_changed(kind, container_id)

                    yield {
                        "event": "tool_result",
                        "data": json.dumps(
                            {
                                "message_id": tool_msg_id,
                                "tool_call_id": call.id,
                                "name": call.name,
                                "arguments": call.arguments,
                                "content": result_text,
                                "is_error": is_error,
                            }
                        ),
                    }

                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=result_text,
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )

            # Hit the iteration cap.
            cap_msg = f"Stopped after {max_tool_rounds} tool rounds."
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


def _lifecycle_stream(
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


@router.post("/stream")
async def stream_chat(
    topic_id: int,
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")

    # Persist the user turn immediately.
    user_msg = Message(topic_id=topic_id, role=MessageRole.USER, content=payload.content)
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)
    await publish_message_changed(topic_id)

    # Bind any pre-uploaded attachments to this user message. We only adopt
    # rows that belong to the same topic and are still unbound, so a stale id
    # from another topic / already-sent turn is silently dropped.
    bound_attachments: list[Attachment] = []
    if payload.attachment_ids:
        att_rows = await session.execute(
            select(Attachment).where(
                Attachment.id.in_(payload.attachment_ids),
                Attachment.topic_id == topic_id,
                Attachment.message_id.is_(None),
            )
        )
        bound_attachments = list(att_rows.scalars().all())
        if bound_attachments:
            await session.execute(
                update(Attachment)
                .where(Attachment.id.in_([a.id for a in bound_attachments]))
                .values(message_id=user_msg.id)
            )
            await session.commit()
            for a in bound_attachments:
                a.message_id = user_msg.id

    # Snapshot history + system context now, before the session closes.
    system_prompt = await _build_system_context(session, topic)
    history_result = await session.execute(
        select(Message)
        .where(Message.topic_id == topic_id)
        .options(selectinload(Message.attachments))
        .order_by(Message.created_at)
    )
    history = _hydrate_history(list(history_result.scalars().all()))

    # For skill invocations: the persisted user message stays the literal
    # slash command (so the chat UI renders /to-en bravo), but the LLM
    # should see the expanded prompt for this turn only.
    if payload.prompt_override:
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].role == "user":
                history[idx] = ChatMessage(
                    role="user",
                    content=payload.prompt_override,
                    image_urls=history[idx].image_urls,
                )
                break

    enabled_servers = await _load_enabled_mcp_servers(session)
    model = payload.model or await resolve_llm_model(session)
    max_tool_rounds = await resolve_max_tool_rounds(session)
    max_input_tokens = await resolve_llm_max_input_tokens(session)
    max_tool_result_tokens = await resolve_llm_max_tool_result_tokens(session)
    provider = await get_llm_provider(session)
    github_token = await resolve_github_token(session)

    user_echo = {
        "id": user_msg.id,
        "content": user_msg.content,
        "attachments": [
            {
                "id": a.id,
                "topic_id": a.topic_id,
                "message_id": a.message_id,
                "mime": a.mime,
                "size": a.size,
                "original_filename": a.original_filename,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in bound_attachments
        ],
    }

    inner = _run_message_stream(
        kind="topic",
        container_id=topic_id,
        system_prompt=system_prompt,
        history=history,
        user_echo=user_echo,
        model=model,
        max_tool_rounds=max_tool_rounds,
        max_input_tokens=max_input_tokens,
        max_tool_result_tokens=max_tool_result_tokens,
        provider=provider,
        github_token=github_token,
        enabled_servers=enabled_servers,
    )
    return EventSourceResponse(_lifecycle_stream("topic", topic_id, inner))
