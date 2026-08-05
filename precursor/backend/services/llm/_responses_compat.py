"""Shared helpers for providers speaking the OpenAI *Responses* API.

The Responses API is the successor to ``/chat/completions``. Newer models
(GPT-5.5 and later, Grok) are served *only* there, so a provider that speaks
just chat-completions can list them but never call them — the API answers
``unsupported_api_for_model``.

Three differences matter for the translation done here:

* history is a flat ``input`` array of *items* rather than ``messages``. Tool
  calls and their results are top-level items correlated by ``call_id``,
  instead of an assistant field plus a ``tool`` message;
* tool definitions are flat — no nested ``function`` object;
* reasoning effort is ``reasoning={"effort": ...}``, not ``reasoning_effort``.

Streaming emits semantic events (``response.output_text.delta``, …) instead of
choice deltas. This module folds them back into Precursor's provider events so
callers can't tell which endpoint served the turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import APIStatusError, AsyncOpenAI

from precursor.backend.services.llm._openai_compat import _friendly_request_error
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


def to_responses_input(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Translate chat messages into Responses ``input`` items.

    An assistant turn that issued tool calls expands into several items: its
    text (when it produced any) followed by one ``function_call`` per call, so
    that the matching ``function_call_output`` items can refer back by
    ``call_id``.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": m.tool_call_id or "",
                    "output": m.content,
                }
            )
            continue

        if m.role == "assistant":
            if m.content:
                out.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": m.content}],
                    }
                )
            for call in m.tool_calls or []:
                fn = call.get("function") or {}
                out.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or "",
                        "name": fn.get("name") or "",
                        "arguments": fn.get("arguments") or "{}",
                    }
                )
            continue

        # system / user: text plus any images, as input content-parts.
        parts: list[dict[str, Any]] = []
        if m.content:
            parts.append({"type": "input_text", "text": m.content})
        for url in m.image_urls:
            parts.append({"type": "input_image", "image_url": url})
        if not parts:
            # An empty content array is rejected; keep the turn's position.
            parts.append({"type": "input_text", "text": ""})
        out.append({"role": m.role, "content": parts})
    return out


def to_responses_tools(tools: Sequence[ToolDef]) -> list[dict[str, Any]]:
    """Tool schemas for the Responses API, which flattens the function object."""
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in tools
    ]


async def stream_responses_tools(
    *,
    client: AsyncOpenAI,
    model: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDef],
    reasoning_effort: str | None = None,
) -> AsyncIterator[ProviderEvent]:
    """Run a tool-aware streamed turn against the Responses API.

    Mirrors ``stream_openai_tools``: text arrives as deltas, tool calls are
    collected and emitted as one ``ToolCallsEvent``, then usage and a done
    marker close the turn.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "input": to_responses_input(messages),
        "stream": True,
    }
    if tools:
        kwargs["tools"] = to_responses_tools(tools)
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}

    try:
        stream = await client.responses.create(**kwargs)
    except APIStatusError as exc:
        raise _friendly_request_error(exc, tool_count=len(tools)) from exc

    calls: list[ToolCallRequest] = []
    usage: UsageEvent | None = None
    status: str | None = None

    async for event in stream:
        etype = getattr(event, "type", "")

        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            if delta:
                yield TextDeltaEvent(content=delta)
            continue

        # Tool calls stream their arguments piecewise, but the completed item
        # carries the whole thing — take it there and skip reassembly.
        if etype == "response.output_item.done":
            item = getattr(event, "item", None)
            if item is not None and getattr(item, "type", "") == "function_call":
                calls.append(
                    ToolCallRequest(
                        id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                        name=getattr(item, "name", "") or "",
                        arguments=getattr(item, "arguments", "") or "{}",
                    )
                )
            continue

        if etype in ("response.completed", "response.incomplete", "response.failed"):
            response = getattr(event, "response", None)
            if response is None:
                continue
            status = getattr(response, "status", None)
            reported = getattr(response, "usage", None)
            if reported is not None:
                usage = UsageEvent(
                    prompt_tokens=getattr(reported, "input_tokens", 0) or 0,
                    completion_tokens=getattr(reported, "output_tokens", 0) or 0,
                    total_tokens=getattr(reported, "total_tokens", 0) or 0,
                )

    if calls:
        yield ToolCallsEvent(calls=calls)
    if usage is not None:
        yield usage
    # Report the chat-completions vocabulary so callers stay endpoint-agnostic.
    if calls:
        finish_reason = "tool_calls"
    elif status == "incomplete":
        finish_reason = "length"
    else:
        finish_reason = "stop"
    yield TurnDoneEvent(finish_reason=finish_reason)
