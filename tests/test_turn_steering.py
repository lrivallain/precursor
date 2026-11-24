"""Messages sent while a topic or chat turn runs (``services/turn_steering.py``).

The running turn takes them at its next round: after the round's tool results,
or after a final answer, which then carries on instead of ending. Once the turn
has ended (or is about to), they are refused so the client sends them as a new
turn.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from precursor.backend.main import create_app
from precursor.backend.services import conversation_turn as conversation_turn_mod
from precursor.backend.services import turn_engine, turn_steering
from precursor.backend.services.llm.base import (
    ChatMessage,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    TurnDoneEvent,
    UsageEvent,
)
from precursor.backend.services.mcp.client import MCPToolDef
from precursor.backend.services.turn_engine import (
    AssistantFinalTurn,
    SteeredTurn,
    ToolResultTurn,
    run_tool_loop,
)

NOTES = MCPToolDef(server="notes", name="write", description="Write a note", input_schema={})


# -- the queue ---------------------------------------------------------------


def test_queue_takes_messages_until_the_turn_closes_it() -> None:
    assert turn_steering.enqueue("chat", 801, "early") is None

    queue = turn_steering.open_queue("chat", 801)
    first = turn_steering.enqueue("chat", 801, "go left")
    assert first is not None and first.content == "go left"
    assert queue.take() == [first]
    assert queue.take() == []
    assert not queue.closed

    # The turn's last look finds nothing: from then on messages are refused.
    assert queue.take(final=True) == []
    assert queue.closed
    assert turn_steering.enqueue("chat", 801, "late") is None

    turn_steering.close_queue("chat", 801, queue)
    assert turn_steering.queue_for("chat", 801) is None


def test_closing_drops_what_the_turn_never_took() -> None:
    queue = turn_steering.open_queue("topic", 802)
    assert turn_steering.enqueue("topic", 802, "unread") is not None
    turn_steering.close_queue("topic", 802, queue)
    assert queue.items == [] and queue.closed
    assert turn_steering.enqueue("topic", 802, "after") is None


def test_closing_a_stale_queue_leaves_the_new_turns_alone() -> None:
    old = turn_steering.open_queue("topic", 803)
    new = turn_steering.open_queue("topic", 803)
    turn_steering.close_queue("topic", 803, old)
    assert turn_steering.queue_for("topic", 803) is new
    turn_steering.close_queue("topic", 803, new)


# -- the tool loop -------------------------------------------------------------


class _Provider:
    """Answers each round from a script; ``on_round`` runs as a round starts."""

    name = "fake"

    def __init__(self, *rounds: list[ToolCallRequest] | str, on_round: Any = None) -> None:
        self.rounds = list(rounds)
        self.requests: list[dict[str, Any]] = []
        self.on_round = on_round

    async def stream_chat_with_tools(self, **request: Any) -> AsyncIterator[Any]:
        self.requests.append(request)
        if self.on_round is not None:
            self.on_round(len(self.requests))
        step = self.rounds.pop(0)
        if isinstance(step, str):
            yield TextDeltaEvent(content=step)
        else:
            yield ToolCallsEvent(calls=step)
        yield TurnDoneEvent(finish_reason="stop")


@pytest.fixture(autouse=True)
def _stub_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(_active: Any, call: Any, _timeout: float) -> AsyncIterator[Any]:
        yield ToolResultTurn(call, f"ran {call.name}", False, None)

    monkeypatch.setattr(turn_engine, "execute_tool_call", execute)


async def _loop(provider: _Provider, steer: turn_steering.SteerQueue | None) -> list[Any]:
    active = SimpleNamespace(tools=[NOTES], tool_to_server={"notes__write": ("notes", "write")})
    return [
        ev
        async for ev in run_tool_loop(
            active=active,
            provider=provider,
            model="m",
            reasoning_effort="low",
            system_prompt="sys",
            history=[ChatMessage(role="user", content="hi")],
            max_tool_rounds=4,
            max_input_tokens=100_000,
            max_tool_result_tokens=10_000,
            steer=steer,
        )
    ]


def _send_on_round(queue: turn_steering.SteerQueue, round_no: int, content: str) -> Any:
    def hook(n: int) -> None:
        if n == round_no:
            queue.items.append(turn_steering.SteerItem(id=f"s{n}", content=content))

    return hook


async def test_a_message_sent_during_a_tool_round_joins_the_next_round() -> None:
    queue = turn_steering.SteerQueue()
    call = ToolCallRequest("c1", "notes__write", "{}")
    provider = _Provider([call], "Done", on_round=_send_on_round(queue, 1, "go left"))
    events = await _loop(provider, queue)

    kinds = [type(ev).__name__ for ev in events if not type(ev).__name__.endswith("Delta")]
    assert kinds == [
        "AssistantToolCallsTurn",
        "ToolResultTurn",
        "SteeredTurn",
        "AssistantFinalTurn",
    ]
    sent = provider.requests[1]["messages"]
    assert [m.role for m in sent[-3:]] == ["assistant", "tool", "user"]
    assert sent[-1].content == "go left"
    assert queue.closed


async def test_a_message_sent_during_the_answer_makes_the_turn_carry_on() -> None:
    queue = turn_steering.SteerQueue()
    provider = _Provider("First", "Second", on_round=_send_on_round(queue, 1, "and also"))
    events = await _loop(provider, queue)

    finals = [ev.text for ev in events if isinstance(ev, AssistantFinalTurn)]
    assert finals == ["First", "Second"]
    steered = next(ev for ev in events if isinstance(ev, SteeredTurn))
    assert [i.content for i in steered.items] == ["and also"]
    sent = provider.requests[1]["messages"]
    assert [(m.role, m.content) for m in sent[-2:]] == [
        ("assistant", "First"),
        ("user", "and also"),
    ]
    assert queue.closed


async def test_without_a_queue_the_loop_ends_on_the_answer() -> None:
    events = await _loop(_Provider("Only"), None)
    assert isinstance(events[-1], AssistantFinalTurn)


# -- end to end: the steer endpoint and the stream -----------------------------


class _SteeringProvider:
    """Its first answer is written while the user sends a message to the topic."""

    name = "steering"

    def __init__(self) -> None:
        self.topic_id = 0
        self.calls = 0

    async def stream_chat(self, **_kwargs: Any) -> AsyncIterator[str]:
        yield ""

    async def stream_chat_with_tools(self, *, messages: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        self.calls += 1
        if self.calls == 1:
            assert turn_steering.enqueue("topic", self.topic_id, "make it shorter") is not None
        yield TextDeltaEvent(content=f"answer to: {messages[-1].content}")
        yield UsageEvent(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        yield TurnDoneEvent(finish_reason="stop")

    async def list_models(self) -> list[Any]:
        return []


def _frames(body: str) -> list[tuple[str, Any]]:
    frames: list[tuple[str, Any]] = []
    event = ""
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            frames.append((event, json.loads(line.split(":", 1)[1].strip())))
    return frames


def test_steer_endpoint_refuses_when_no_turn_runs() -> None:
    with TestClient(create_app()) as client:
        tid = client.post("/api/topics", json={"title": "Idle"}).json()["id"]
        res = client.post(f"/api/topics/{tid}/messages/steer", json={"content": "hello"})
        assert res.status_code == 200
        assert res.json() == {"queued": False, "steer_id": None}
        cid = client.post("/api/chats", json={"title": "Idle"}).json()["id"]
        res = client.post(f"/api/chats/{cid}/messages/steer", json={"content": "hello"})
        assert res.json() == {"queued": False, "steer_id": None}
        assert (
            client.post("/api/chats/999999/messages/steer", json={"content": "x"}).status_code
            == 404
        )


def test_a_steered_message_is_answered_in_the_same_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _SteeringProvider()

    async def get_provider(_session: Any) -> Any:
        return provider

    async def no_usage(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(conversation_turn_mod, "get_llm_provider", get_provider)
    monkeypatch.setattr(turn_engine, "record_usage", no_usage)
    with TestClient(create_app()) as client:
        tid = client.post("/api/topics", json={"title": "Steer"}).json()["id"]
        provider.topic_id = tid
        res = client.post(
            f"/api/topics/{tid}/messages/stream",
            json={"content": "write a poem"},
            headers={"Accept": "text/event-stream"},
        )
        frames = _frames(res.text)
        echoes = [data for event, data in frames if event == "user_message"]
        assert [e["content"] for e in echoes] == ["write a poem", "make it shorter"]
        assert "steer_id" not in echoes[0] and echoes[1]["steer_id"]
        done = [data["content"] for event, data in frames if event == "done"]
        assert done == ["answer to: write a poem", "answer to: make it shorter"]

        rows = client.get(f"/api/topics/{tid}/messages").json()
        assert [(r["role"], r["content"]) for r in rows] == [
            ("user", "write a poem"),
            ("assistant", "answer to: write a poem"),
            ("user", "make it shorter"),
            ("assistant", "answer to: make it shorter"),
        ]
        assert turn_steering.queue_for("topic", tid) is None
