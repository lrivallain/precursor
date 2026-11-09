"""Branch coverage for the turn engine's tool loop and its SSE consumer.

``run_tool_loop`` is driven with a scripted provider and a stub tool runner;
``run_message_stream`` with a scripted tool loop and stubbed persistence, so the
exact SSE frames each kind of event turns into are pinned.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from precursor.backend.services import approvals as approval_service
from precursor.backend.services import turn_engine
from precursor.backend.services.approvals import ApprovalGate
from precursor.backend.services.llm.base import (
    ChatMessage,
    LLMError,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    ToolDef,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.mcp.client import MCPToolDef
from precursor.backend.services.turn_engine import (
    ApprovalsRequested,
    AssistantFinalTurn,
    AssistantReasoningDelta,
    AssistantTextDelta,
    AssistantToolCallsTurn,
    RoundCapReached,
    ToolAuthRequired,
    ToolResultTurn,
    run_message_stream,
    run_tool_loop,
)

USAGE = UsageEvent(prompt_tokens=3, completion_tokens=2, total_tokens=5)
NOTES = MCPToolDef(server="notes", name="write", description="Write a note", input_schema={})


class _Provider:
    """Answers each round from a script: a list of tool calls, or text."""

    name = "fake"

    def __init__(self, *rounds: list[ToolCallRequest] | str) -> None:
        self.rounds = list(rounds)
        self.requests: list[dict[str, Any]] = []

    async def stream_chat_with_tools(self, **request: Any) -> AsyncIterator[Any]:
        self.requests.append(request)
        step = (
            self.rounds.pop(0) if self.rounds else [ToolCallRequest("again", "notes__write", "{}")]
        )
        yield ReasoningDeltaEvent(content="hmm")
        if isinstance(step, str):
            yield TextDeltaEvent(content=step)
        else:
            yield TextDeltaEvent(content="calling")
            yield ToolCallsEvent(calls=step)
        yield USAGE
        yield TurnDoneEvent(finish_reason="stop")


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    ran: list[str] = []

    async def execute(_active: Any, call: Any, _timeout: float) -> AsyncIterator[Any]:
        ran.append(call.name)
        if call.arguments == '{"auth": true}':
            yield ToolAuthRequired(server="notes", message="sign in", tool=call.name)
        yield ToolResultTurn(call, f"ran {call.name}", False, None)

    monkeypatch.setattr(turn_engine, "execute_tool_call", execute)
    return ran


def _active() -> Any:
    return SimpleNamespace(tools=[NOTES], tool_to_server={"notes__write": ("notes", "write")})


async def _loop(provider: _Provider, **options: Any) -> list[Any]:
    kwargs: dict[str, Any] = {
        "active": _active(),
        "provider": provider,
        "model": "m",
        "reasoning_effort": "low",
        "system_prompt": "sys",
        "history": [ChatMessage(role="user", content="hi")],
        "max_tool_rounds": 3,
        "max_input_tokens": 100_000,
        "max_tool_result_tokens": 10_000,
        **options,
    }
    return [ev async for ev in run_tool_loop(**kwargs)]


def _call(call_id: str, name: str = "notes__write", arguments: str = "{}") -> ToolCallRequest:
    return ToolCallRequest(call_id, name, arguments)


async def test_plain_answer_streams_deltas_then_the_final_turn(executed: list[str]) -> None:
    events = await _loop(_Provider("Hello"))
    assert events == [
        AssistantReasoningDelta("hmm"),
        AssistantTextDelta("Hello"),
        AssistantFinalTurn("Hello", USAGE, "hmm", "m", "fake"),
    ]


async def test_tool_round_feeds_results_back_until_the_answer(executed: list[str]) -> None:
    provider = _Provider([_call("c1", arguments='{"auth": true}')], "Done")
    events = await _loop(provider)
    kinds = [type(ev).__name__ for ev in events]
    assert kinds == [
        "AssistantReasoningDelta",
        "AssistantTextDelta",
        "AssistantToolCallsTurn",
        "ToolAuthRequired",
        "ToolResultTurn",
        "AssistantReasoningDelta",
        "AssistantTextDelta",
        "AssistantFinalTurn",
    ]
    calls_turn = events[2]
    assert isinstance(calls_turn, AssistantToolCallsTurn)
    assert calls_turn.openai_tool_calls == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "notes__write", "arguments": '{"auth": true}'},
        }
    ]
    replay = provider.requests[1]["messages"]
    assert [m.role for m in replay[-2:]] == ["assistant", "tool"]
    assert replay[-1].content == "ran notes__write"


async def test_round_cap_is_reported(executed: list[str]) -> None:
    events = await _loop(_Provider(), max_tool_rounds=2)
    assert events[-1] == RoundCapReached(2)
    assert executed == ["notes__write", "notes__write"]


async def test_wrap_up_withdraws_tools_on_the_last_round(executed: list[str]) -> None:
    provider = _Provider([_call("c1")], "Summary")
    events = await _loop(provider, max_tool_rounds=2, wrap_up="Answer now.")
    assert isinstance(events[-1], AssistantFinalTurn)
    assert provider.requests[1]["tools"] == []
    assert provider.requests[1]["messages"][-1].content == "Answer now."
    with pytest.raises(HTTPException, match="after tools were withdrawn"):
        await _loop(_Provider([_call("c1")], [_call("c2")]), max_tool_rounds=2, wrap_up="Stop.")


async def test_required_message_must_survive_trimming(executed: list[str]) -> None:
    required = ChatMessage(role="user", content="input " * 5000)
    with pytest.raises(HTTPException, match="no longer fits"):
        await _loop(
            _Provider("x"),
            history=[required, ChatMessage(role="user", content="go")],
            required_history_message=required,
            max_input_tokens=200,
        )


async def test_gated_extra_tools_need_a_decline_handler() -> None:
    with pytest.raises(ValueError, match="decline handler"):
        await _loop(_Provider("x"), gate=ApprovalGate(), extra_permissions={"x": NOTES})


async def test_manual_policy_parks_calls_after_running_the_rest(executed: list[str]) -> None:
    provider = _Provider([_call("c1"), _call("c2", "unknown__tool")])
    events = await _loop(provider, gate=ApprovalGate(policy=approval_service.POLICY_MANUAL))
    assert executed == ["unknown__tool"]
    parked = events[-1]
    assert isinstance(parked, ApprovalsRequested)
    assert [(p.call_id, p.server, p.raw_tool) for p in parked.items] == [("c1", "notes", "write")]
    assert parked.items[0].reason == "Manual policy: every tool call needs your approval."
    tool_names = [t.name for t in provider.requests[0]["tools"]]
    assert approval_service.REQUEST_APPROVAL_TOOL not in tool_names


async def test_request_approval_parks_an_intent_when_offered(executed: list[str]) -> None:
    ask = _call("c1", approval_service.REQUEST_APPROVAL_TOOL, '{"reason": "Delete?"}')
    provider = _Provider([ask])
    events = await _loop(provider, gate=ApprovalGate())
    assert executed == []
    parked = events[-1]
    assert isinstance(parked, ApprovalsRequested)
    assert [p.call_id for p in parked.items] == ["c1"]
    assert approval_service.APPROVAL_PROMPT in provider.requests[0]["messages"][0].content


async def test_request_approval_runs_like_any_call_when_not_offered(executed: list[str]) -> None:
    ask = _call("c1", approval_service.REQUEST_APPROVAL_TOOL)
    events = await _loop(
        _Provider([ask], "ok"), gate=ApprovalGate(policy=approval_service.POLICY_MANUAL)
    )
    assert executed == [approval_service.REQUEST_APPROVAL_TOOL]
    assert isinstance(events[-1], AssistantFinalTurn)


async def test_decline_note_answers_held_calls_instead_of_parking(executed: list[str]) -> None:
    provider = _Provider([_call("c1")], "fine")
    events = await _loop(
        provider,
        gate=ApprovalGate(policy=approval_service.POLICY_MANUAL),
        decline_note="Nobody can approve it.",
    )
    declined = next(ev for ev in events if isinstance(ev, ToolResultTurn))
    assert declined.declined and declined.is_error
    assert declined.result_text == (
        "Not run: Manual policy: every tool call needs your approval. Nobody can approve it."
    )
    assert provider.requests[1]["messages"][-1].content == declined.result_text
    assert executed == []


async def test_extra_tools_run_locally_and_are_gated_by_their_identity(
    executed: list[str],
) -> None:
    local = ToolDef(name="peek", description="Peek", parameters={})
    seen: list[str] = []

    async def run_extra(call: Any) -> str:
        seen.append(call.id)
        return "peeked"

    events = await _loop(
        _Provider([_call("c1", "peek"), _call("c2", "peek")], "done"),
        extra_tools=[local],
        run_extra=run_extra,
    )
    assert seen == ["c1", "c2"] and executed == []
    assert [ev.result_text for ev in events if isinstance(ev, ToolResultTurn)] == ["peeked"] * 2

    events = await _loop(
        _Provider([_call("c1", "peek")], "done"),
        extra_tools=[local],
        run_extra=run_extra,
        extra_permissions={"peek": NOTES},
        gate=ApprovalGate(policy=approval_service.POLICY_MANUAL),
        decline_note="No.",
    )
    assert next(ev for ev in events if isinstance(ev, ToolResultTurn)).declined


# -- run_message_stream --------------------------------------------------------


class _Manager:
    def __init__(self) -> None:
        self.active = SimpleNamespace(
            unavailable=[("cached", "down"), ("broken", "refused")],
            advertised_from_cache={"cached"},
            restrict_server_tools=lambda *_a: None,
        )

    @asynccontextmanager
    async def acquired(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        yield self.active


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"events": [], "persisted": [], "loop_kwargs": None}
    monkeypatch.setattr(turn_engine, "get_mcp_client_manager", _Manager)

    async def loop(**kwargs: Any) -> AsyncIterator[Any]:
        state["loop_kwargs"] = kwargs
        for ev in state["events"]:
            if isinstance(ev, Exception):
                raise ev
            yield ev

    async def persist_final(kind: str, cid: int, ev: Any, *, model: str, elapsed_ms: int) -> Any:
        state["persisted"].append(("final", kind, model))
        return turn_engine.PersistedAnswer(10, "pub-10", ev.text, ["Next?"])

    async def persist_calls(kind: str, cid: int, ev: Any, *, model: str) -> tuple[int, str]:
        state["persisted"].append(("calls", kind, model))
        return 7, "pub-7"

    async def persist_result(kind: str, cid: int, ev: Any) -> int:
        return 8

    async def system_message(cid: int, text: str, *, kind: str) -> None:
        state["persisted"].append(("system", kind, text))

    async def record_pending(
        _ws: Any, kind: str, cid: int, assistant_id: Any, items: Any, **kw: Any
    ) -> list[Any]:
        state["pending"] = (assistant_id, kw)
        return ["row"]

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(turn_engine, "run_tool_loop", loop)
    monkeypatch.setattr(turn_engine, "persist_final_turn", persist_final)
    monkeypatch.setattr(turn_engine, "persist_tool_calls_turn", persist_calls)
    monkeypatch.setattr(turn_engine, "persist_tool_result", persist_result)
    monkeypatch.setattr(turn_engine, "_persist_system_message", system_message)
    monkeypatch.setattr(turn_engine, "publish_approval_changed", nothing)
    monkeypatch.setattr(approval_service, "record_pending", record_pending)
    monkeypatch.setattr(approval_service, "serialize", lambda row: {"row": row})
    from precursor.backend.services import step_plan_llm

    monkeypatch.setattr(step_plan_llm, "apply_chat_reply", nothing)
    return state


async def _frames(kind: str = "topic", user_echo: Any = None) -> list[tuple[str, Any]]:
    frames = run_message_stream(
        kind=kind,  # type: ignore[arg-type]
        container_id=5,
        system_prompt="sys",
        history=[],
        user_echo=user_echo,
        model="m",
        reasoning_effort="low",
        max_tool_rounds=3,
        max_input_tokens=1000,
        max_tool_result_tokens=100,
        provider=object(),
        github_token="",
        enabled_servers=[],
    )
    return [(f["event"], json.loads(f["data"])) async for f in frames]


async def test_stream_relays_each_event_as_its_frame(stream: dict[str, Any]) -> None:
    call = _call("c1")
    stream["events"] = [
        AssistantTextDelta("Hi"),
        AssistantReasoningDelta("think"),
        AssistantToolCallsTurn("", [call], [], USAGE, model="m2"),
        ToolResultTurn(call, "res", False, {"slug": "w", "path": "a"}),
        ToolAuthRequired(server="notes", message="sign in", tool="notes__write"),
        AssistantFinalTurn("Hello", USAGE, "", "m3", "fake"),
        AssistantTextDelta("never relayed"),
    ]
    frames = await _frames("chat", user_echo={"id": 42, "content": "hi"})
    usage = {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert frames[0] == ("user_message", {"id": 42, "content": "hi"})
    assert frames[1] == ("system", {"message": "MCP server 'broken' unavailable: refused"})
    assert frames[2:] == [
        ("delta", {"content": "Hi"}),
        ("reasoning", {"content": "think"}),
        ("usage", {"message_id": 7, **usage}),
        (
            "tool_calls",
            {
                "assistant_id": 7,
                "public_id": "pub-7",
                "calls": [{"id": "c1", "name": "notes__write", "arguments": "{}"}],
            },
        ),
        (
            "tool_result",
            {
                "message_id": 8,
                "tool_call_id": "c1",
                "name": "notes__write",
                "arguments": "{}",
                "content": "res",
                "is_error": False,
                "link": {"slug": "w", "path": "a"},
            },
        ),
        ("mcp_auth_required", {"server": "notes", "message": "sign in", "tool": "notes__write"}),
        ("usage", {"message_id": 10, **usage}),
        (
            "done",
            {
                "id": 10,
                "public_id": "pub-10",
                "content": "Hello",
                "model": "m3",
                "provider": "fake",
                "elapsed_ms": frames[-2][1]["elapsed_ms"],
            },
        ),
        ("suggestions", {"message_id": 10, "items": ["Next?"]}),
    ]
    assert stream["persisted"] == [("calls", "chat", "m2"), ("final", "chat", "m3")]
    assert stream["loop_kwargs"]["source_message"] == ("chat", 5, 42)


async def test_stream_parks_on_approvals_with_the_last_assistant_turn(
    stream: dict[str, Any],
) -> None:
    call = _call("c1")
    stream["events"] = [
        AssistantToolCallsTurn("", [call], [], None),
        ApprovalsRequested([]),
        AssistantTextDelta("never relayed"),
    ]
    frames = await _frames()
    assert frames[-1] == ("approval_required", {"approvals": [{"row": "row"}]})
    assert stream["pending"] == (7, {"source_message_id": None})
    assert stream["loop_kwargs"]["source_message"] is None


@pytest.mark.parametrize(
    ("event", "message", "persisted"),
    [
        (RoundCapReached(4), "Stopped after 4 tool rounds.", "Error: Stopped after 4 tool rounds."),
        (LLMError("Too many tools"), "Too many tools", "Error: Too many tools"),
        (RuntimeError("boom"), "boom", "Error: boom"),
    ],
)
async def test_stream_reports_errors_and_keeps_them(
    stream: dict[str, Any], event: Any, message: str, persisted: str
) -> None:
    stream["events"] = [event]
    frames = await _frames()
    assert frames[-1] == ("error", {"message": message})
    assert stream["persisted"] == [("system", "topic", persisted)]
