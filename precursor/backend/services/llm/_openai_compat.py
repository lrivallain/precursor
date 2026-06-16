"""Shared helpers for OpenAI-compatible chat providers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import AsyncOpenAI

from precursor.backend.services.llm.base import (
    ChatMessage,
    ProviderEvent,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)

logger = logging.getLogger(__name__)

# OpenAI / Azure OpenAI reject a request whose ``tools`` array exceeds 128
# entries. With many MCP servers enabled the aggregate easily passes that, so
# we cap it (keeping the first N) rather than letting the API 400 the turn.
_MAX_TOOLS = 128


def to_openai_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        d: dict[str, Any] = {"role": m.role}
        if m.role == "user" and m.image_urls:
            # Vision-capable models expect a content-parts array. Text is
            # optional, so skip the text block entirely when empty rather than
            # sending `{"type":"text","text":""}` (some backends reject it).
            parts: list[dict[str, Any]] = []
            if m.content:
                parts.append({"type": "text", "text": m.content})
            for url in m.image_urls:
                parts.append({"type": "image_url", "image_url": {"url": url}})
            d["content"] = parts
        else:
            d["content"] = m.content
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        out.append(d)
    return out


def to_openai_tools(tools: Sequence[ToolDef]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


async def stream_openai_tools(
    *,
    client: AsyncOpenAI,
    model: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDef],
) -> AsyncIterator[ProviderEvent]:
    """Run a tool-aware streamed completion against an OpenAI-compatible API.

    Accumulates ``tool_calls`` deltas (which arrive piecewise — ``id`` and
    ``name`` come on the first delta, ``arguments`` are concatenated across
    subsequent ones) and emits them as a single ``ToolCallsEvent`` once the
    turn completes.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": to_openai_messages(messages),
        "stream": True,
        # Final chunk includes a ``usage`` object with prompt/completion tokens.
        "stream_options": {"include_usage": True},
    }
    if tools:
        capped = tools[:_MAX_TOOLS]
        if len(tools) > _MAX_TOOLS:
            logger.warning(
                "Tool count %d exceeds the %d-tool API limit; sending the first %d. "
                "Disable some MCP servers to control which tools are available.",
                len(tools),
                _MAX_TOOLS,
                _MAX_TOOLS,
            )
        kwargs["tools"] = to_openai_tools(capped)

    stream = await client.chat.completions.create(**kwargs)
    # tool_call_id -> {id, name, arguments}
    pending: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    usage: UsageEvent | None = None

    async for chunk in stream:
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = UsageEvent(
                prompt_tokens=getattr(chunk_usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(chunk_usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(chunk_usage, "total_tokens", 0) or 0,
            )
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if delta is None:
            continue

        if delta.content:
            yield TextDeltaEvent(content=delta.content)

        for tc in delta.tool_calls or []:
            slot = pending.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            fn = tc.function
            if fn is not None:
                if fn.name:
                    slot["name"] = fn.name
                if fn.arguments:
                    slot["arguments"] += fn.arguments

        if choice.finish_reason:
            finish_reason = choice.finish_reason

    if pending:
        calls = [
            ToolCallRequest(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
            for idx, slot in sorted(pending.items())
        ]
        yield ToolCallsEvent(calls=calls)

    if usage is not None:
        yield usage
    yield TurnDoneEvent(finish_reason=finish_reason)
