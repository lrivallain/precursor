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
  The same object asks for a reasoning *summary*, the only view of the model's
  thinking this API streams.

Streaming emits semantic events (``response.output_text.delta``, …) instead of
choice deltas. This module folds them back into Precursor's provider events so
callers can't tell which endpoint served the turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from openai import AsyncOpenAI

from precursor.backend.services.llm._openai_compat import open_stream_with_retry
from precursor.backend.services.llm.base import (
    ChatMessage,
    ProviderEvent,
    ReasoningDeltaEvent,
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


# Chat-completions request options this API takes under the same name.
_SAME_NAME_OPTIONS = ("temperature", "top_p", "parallel_tool_calls")


def to_responses_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Translate chat-completions request options to their Responses spelling.

    Options with no Responses equivalent (``stop``, ``seed``, the penalties) are
    dropped: the caller asked for a chat-completions model and this is the best
    rendition of that request, not a place to fail it.
    """
    if not options:
        return {}
    out: dict[str, Any] = {k: options[k] for k in _SAME_NAME_OPTIONS if k in options}

    max_tokens = options.get("max_completion_tokens", options.get("max_tokens"))
    if max_tokens is not None:
        out["max_output_tokens"] = max_tokens

    choice = options.get("tool_choice")
    if isinstance(choice, str):
        out["tool_choice"] = choice
    elif isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name")
        if name:
            out["tool_choice"] = {"type": "function", "name": name}

    fmt = options.get("response_format")
    if isinstance(fmt, dict):
        kind = fmt.get("type")
        if kind == "json_object":
            out["text"] = {"format": {"type": "json_object"}}
        elif kind == "json_schema":
            spec = fmt.get("json_schema") or {}
            out["text"] = {
                "format": {
                    "type": "json_schema",
                    **{
                        k: spec[k] for k in ("name", "description", "schema", "strict") if k in spec
                    },
                }
            }
    return out


async def stream_responses_tools(
    *,
    client: AsyncOpenAI,
    model: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDef],
    reasoning_effort: str | None = None,
    request_options: Mapping[str, Any] | None = None,
) -> AsyncIterator[ProviderEvent]:
    """Run a tool-aware streamed turn against the Responses API.

    Mirrors ``stream_openai_tools``: text arrives as deltas, tool calls are
    collected and emitted as one ``ToolCallsEvent``, then usage and a done
    marker close the turn.
    """
    kwargs: dict[str, Any] = {
        **to_responses_options(request_options),
        "model": model,
        "input": to_responses_input(messages),
        "stream": True,
    }
    if tools:
        kwargs["tools"] = to_responses_tools(tools)
    # Always ask for the summary: models that don't reason just stream none.
    reasoning: dict[str, str] = {"summary": "auto"}
    if reasoning_effort:
        reasoning["effort"] = reasoning_effort
    kwargs["reasoning"] = reasoning

    stream = await open_stream_with_retry(
        lambda: client.responses.create(**kwargs), tool_count=len(tools)
    )

    calls: list[ToolCallRequest] = []
    usage: UsageEvent | None = None
    status: str | None = None
    reasoned = False

    async for event in stream:
        etype = getattr(event, "type", "")

        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            if delta:
                yield TextDeltaEvent(content=delta)
            continue

        if etype == "response.reasoning_summary_text.delta":
            delta = getattr(event, "delta", "")
            if delta:
                reasoned = True
                yield ReasoningDeltaEvent(content=delta)
            continue

        # Each summary part opens with its own bold heading; without a break
        # between them the next heading would run on from the last sentence.
        if etype == "response.reasoning_summary_part.added":
            if reasoned:
                yield ReasoningDeltaEvent(content="\n\n")
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
