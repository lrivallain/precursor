"""Claude's native Messages API adapter (``llm/_anthropic_messages.py``)."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic

from precursor.backend.services.llm._anthropic_messages import (
    _PREFIX_KEY,
    _prefix_fingerprint,
    stream_anthropic_tools,
    to_anthropic_options,
    to_anthropic_request,
    to_anthropic_tools,
)
from precursor.backend.services.llm.azure_foundry import foundry_messages_client
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)

_CACHE = {"type": "ephemeral"}


def _call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_request_conversion_groups_tool_results_and_caches_the_system_prompt() -> None:
    system, messages = to_anthropic_request(
        [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="Look", image_urls=["data:image/png;base64,AAAA"]),
            ChatMessage(
                role="assistant",
                content="Checking.",
                tool_calls=[_call("a", "t1", {"x": 1}), _call("b", "t2", {})],
            ),
            ChatMessage(role="tool", content="one", tool_call_id="a", name="t1"),
            ChatMessage(role="tool", content="", tool_call_id="b", name="t2"),
            ChatMessage(role="system", content="Turn failed."),
            ChatMessage(role="user", content="Again"),
        ]
    )
    assert system == [{"type": "text", "text": "You are helpful.", "cache_control": _CACHE}]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "AAAA",
    }
    assert messages[1]["content"] == [
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "a", "name": "t1", "input": {"x": 1}},
        {"type": "tool_use", "id": "b", "name": "t2", "input": {}},
    ]
    results = messages[2]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result", "text", "text"]
    assert results[1]["content"] == "(no output)"
    assert results[2]["text"].startswith("[System note]")
    # The history before the per-turn note is cached on its own.
    assert results[1]["cache_control"] == _CACHE
    assert sum("cache_control" in b for m in messages for b in m["content"]) == 1


def _signed_turn(tools: list[ToolDef], history: list[ChatMessage]) -> ChatMessage:
    """The assistant turn Claude would return after ``history``, as the loop stores it."""
    system, chat = to_anthropic_request(history, tools)
    prefix = _prefix_fingerprint(tools, system, chat)
    return ChatMessage(
        role="assistant",
        content="",
        tool_calls=[_call("a", "t1", {"x": 1})],
        provider_content=[
            {"type": "thinking", "thinking": "hmm", "signature": "sig", _PREFIX_KEY: prefix},
            {"type": "tool_use", "id": "a", "name": "t1", "input": {"x": 1}},
        ],
    )


_T1 = ToolDef(name="t1", description="one", parameters={"type": "object"})
_T2 = ToolDef(name="t2", description="two", parameters={"type": "object"})


def test_signed_turn_is_replayed_verbatim_on_the_same_prefix() -> None:
    history = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="q")]
    turn = _signed_turn([_T1], history)
    result = ChatMessage(role="tool", content="ok", tool_call_id="a", name="t1")
    _, messages = to_anthropic_request([*history, turn, result], [_T1])
    assert messages[1]["content"] == [
        {"type": "thinking", "thinking": "hmm", "signature": "sig"},
        {"type": "tool_use", "id": "a", "name": "t1", "input": {"x": 1}},
    ]


def test_thinking_is_dropped_once_the_prefix_changed() -> None:
    history = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="q")]
    turn = _signed_turn([_T1], history)
    result = ChatMessage(role="tool", content="ok", tool_call_id="a", name="t1")
    # A tool loaded on demand changes the tools list the block was bound to…
    _, loaded = to_anthropic_request([*history, turn, result], [_T1, _T2])
    # …and so does trimmed or edited history before it.
    edited = [history[0], ChatMessage(role="user", content="q, edited")]
    _, trimmed = to_anthropic_request([*edited, turn, result], [_T1])
    for messages in (loaded, trimmed):
        assert messages[1]["content"] == [
            {"type": "tool_use", "id": "a", "name": "t1", "input": {"x": 1}}
        ]


def test_last_tool_carries_the_cache_breakpoint() -> None:
    tools = to_anthropic_tools(
        [
            ToolDef(name="a", description="A", parameters={}),
            ToolDef(name="b", description="B", parameters={}),
        ]
    )
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == _CACHE


def test_options_drop_sampling_and_soften_forced_tool_choice() -> None:
    out = to_anthropic_options(
        {
            "temperature": 0.2,
            "max_tokens": 500,
            "tool_choice": {"type": "function", "function": {"name": "x"}},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"schema": {"type": "object"}},
            },
            "stop": "END",
        }
    )
    assert out == {
        "max_tokens": 500,
        "tool_choice": {"type": "auto"},
        "output_config": {"format": {"type": "json_schema", "schema": {"type": "object"}}},
        "stop_sequences": ["END"],
    }


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _stream_events() -> list[dict[str, Any]]:
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 900,
                    "cache_creation_input_tokens": 90,
                    "output_tokens": 1,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "plan"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Let me check."},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "tool_use", "id": "tu", "name": "weather", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"city": "Paris"}'},
        },
        {"type": "content_block_stop", "index": 2},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 42},
        },
        {"type": "message_stop"},
    ]


def _client(handler) -> AsyncAnthropic:  # type: ignore[no-untyped-def]
    return foundry_messages_client(
        "https://res.openai.azure.com/anthropic/",
        api_key="k",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def test_stream_translates_events_usage_and_replay_blocks() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(_stream_events())
        )

    events = [
        ev
        async for ev in stream_anthropic_tools(
            client=_client(handler),
            model="claude-opus-5-5",
            messages=[
                ChatMessage(role="system", content="sys"),
                ChatMessage(role="user", content="hi"),
            ],
            tools=[ToolDef(name="weather", description="w", parameters={"type": "object"})],
            reasoning_effort="minimal",
        )
    ]
    body = sent[0]
    assert body["cache_control"] == _CACHE
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "low"}
    assert body["tools"][-1]["cache_control"] == _CACHE
    assert body["system"][-1]["cache_control"] == _CACHE

    assert ReasoningDeltaEvent(content="plan") in events
    assert TextDeltaEvent(content="Let me check.") in events
    calls = next(ev for ev in events if isinstance(ev, ToolCallsEvent))
    assert [(c.id, c.name, json.loads(c.arguments)) for c in calls.calls] == [
        ("tu", "weather", {"city": "Paris"})
    ]
    thinking = calls.provider_content[0]
    assert thinking.pop(_PREFIX_KEY) == _prefix_fingerprint(
        [ToolDef(name="weather", description="w", parameters={"type": "object"})],
        [{"type": "text", "text": "sys"}],
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    )
    assert calls.provider_content == [
        {"type": "thinking", "thinking": "plan", "signature": "sig"},
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "tu", "name": "weather", "input": {"city": "Paris"}},
    ]
    usage = next(ev for ev in events if isinstance(ev, UsageEvent))
    assert (usage.prompt_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (
        1000,
        900,
        90,
    )
    assert usage.completion_tokens == 42
    assert events[-1] == TurnDoneEvent(finish_reason="tool_calls")


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "credentials"), (429, "rate limit"), (400, "rejected the request")],
)
async def test_http_errors_become_llm_errors_with_status(status: int, message: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": "x", "message": "nope"}}
        )

    with pytest.raises(LLMError, match=message) as caught:
        async for _ in stream_anthropic_tools(
            client=_client(handler),
            model="claude-opus-5-5",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[],
        ):
            pass
    assert getattr(caught.value.__cause__, "status_code", None) == status
