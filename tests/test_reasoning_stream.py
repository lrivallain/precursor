"""The model's thinking, from the provider wire formats to the transcript.

Reasoning models stream what they are thinking before they answer. Precursor
shows it in a collapsed "Thinking" area, so it has to be read off each API
surface, streamed as its own SSE event, and stored with the assistant round it
belongs to — without ever being replayed to the model as history.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from openai.types.chat import ChatCompletionChunk
from sqlalchemy import select

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import Message, MessageRole, Topic
from precursor.backend.services import turn_engine as turn_engine_mod
from precursor.backend.services.llm._openai_compat import stream_openai_tools
from precursor.backend.services.llm._responses_compat import stream_responses_tools
from precursor.backend.services.llm.base import (
    ChatMessage,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolCallRequest,
    ToolCallsEvent,
    TurnDoneEvent,
)
from precursor.backend.services.mcp.client import ActiveTools, MCPClientManager

# -- Provider wire formats --------------------------------------------------


class _Create:
    """Stands in for an SDK ``create``: records the kwargs, replays ``items``."""

    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs

        async def _stream() -> Any:
            for item in self.items:
                yield item

        return _stream()


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
    )


async def _chat_completion_events(chunks: list[ChatCompletionChunk]) -> list[Any]:
    create = _Create(chunks)
    client: Any = SimpleNamespace(chat=SimpleNamespace(completions=create))
    messages = [ChatMessage(role="user", content="hi")]
    return [
        e async for e in stream_openai_tools(client=client, model="m", messages=messages, tools=())
    ]


async def test_chat_completions_reads_each_servers_reasoning_field() -> None:
    events = await _chat_completion_events(
        [
            # Copilot (Claude, Gemini)
            _chunk({"role": "assistant", "reasoning_text": "**Checking** "}),
            # DeepSeek-style servers
            _chunk({"reasoning_content": "factors, "}),
            # OpenRouter / Ollama
            _chunk({"reasoning": "then answer."}),
            _chunk({"content": "No."}, finish_reason="stop"),
        ]
    )

    reasoning = [e.content for e in events if isinstance(e, ReasoningDeltaEvent)]
    assert reasoning == ["**Checking** ", "factors, ", "then answer."]
    assert [e.content for e in events if isinstance(e, TextDeltaEvent)] == ["No."]
    # Thinking arrives ahead of the answer, as the UI expects.
    first_text = next(i for i, e in enumerate(events) if isinstance(e, TextDeltaEvent))
    assert all(isinstance(e, ReasoningDeltaEvent) for e in events[:first_text])


async def test_chat_completions_reads_one_field_when_a_server_mirrors_them() -> None:
    events = await _chat_completion_events(
        [_chunk({"reasoning_content": "same", "reasoning": "same"}), _chunk({"content": "ok"})]
    )

    assert [e.content for e in events if isinstance(e, ReasoningDeltaEvent)] == ["same"]


def _responses_event(etype: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=etype, **fields)


async def _responses_run(reasoning_effort: str | None) -> tuple[list[Any], dict[str, Any]]:
    create = _Create(
        [
            _responses_event("response.reasoning_summary_part.added", summary_index=0),
            _responses_event("response.reasoning_summary_text.delta", delta="**Planning**"),
            _responses_event("response.reasoning_summary_part.added", summary_index=1),
            _responses_event("response.reasoning_summary_text.delta", delta="**Checking**"),
            _responses_event("response.output_text.delta", delta="Done."),
            _responses_event(
                "response.completed", response=SimpleNamespace(status="completed", usage=None)
            ),
        ]
    )
    client: Any = SimpleNamespace(responses=create)
    events = [
        e
        async for e in stream_responses_tools(
            client=client,
            model="m",
            messages=[ChatMessage(role="user", content="hi")],
            tools=(),
            reasoning_effort=reasoning_effort,
        )
    ]
    return events, create.kwargs


async def test_responses_streams_the_summary_with_parts_kept_apart() -> None:
    events, _ = await _responses_run("high")

    reasoning = "".join(e.content for e in events if isinstance(e, ReasoningDeltaEvent))
    # Each part opens with its own heading; they must not run together.
    assert reasoning == "**Planning**\n\n**Checking**"
    assert [e.content for e in events if isinstance(e, TextDeltaEvent)] == ["Done."]
    assert isinstance(events[-1], TurnDoneEvent)


async def test_responses_always_asks_for_the_summary() -> None:
    _, with_effort = await _responses_run("high")
    _, without_effort = await _responses_run(None)

    # The Responses API streams no thinking at all unless a summary is requested.
    assert with_effort["reasoning"] == {"summary": "auto", "effort": "high"}
    assert without_effort["reasoning"] == {"summary": "auto"}


# -- Turn engine: SSE + persistence ----------------------------------------


class _ThinksThenAnswers:
    """Thinks and calls a tool on round one, thinks and answers on round two."""

    name = "fake"

    def __init__(self) -> None:
        self.rounds = 0
        self.seen: list[list[ChatMessage]] = []

    async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):  # type: ignore[no-untyped-def]
        _ = model, tools, reasoning_effort
        self.seen.append(list(messages))
        self.rounds += 1
        if self.rounds == 1:
            yield ReasoningDeltaEvent(content="I need to look it up.")
            yield ToolCallsEvent(
                calls=[ToolCallRequest(id="call-1", name="nowhere__x", arguments="{}")]
            )
        else:
            yield ReasoningDeltaEvent(content="**Answering**")
            yield ReasoningDeltaEvent(content="\n\nKeep it short.")
            yield TextDeltaEvent(content="Here you go.")
        yield TurnDoneEvent(finish_reason="stop")


def _install_empty_manager(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    manager = MCPClientManager()

    class _Ctx:
        async def __aenter__(self) -> ActiveTools:
            return ActiveTools(manager=manager)

        async def __aexit__(self, *exc: object) -> bool:
            return False

    def acquired(names: list[str], *, github_token: str = "", advertise_cached: bool = False):  # type: ignore[no-untyped-def]
        _ = names, github_token, advertise_cached
        return _Ctx()

    manager.acquired = acquired  # type: ignore[assignment]
    monkeypatch.setattr(turn_engine_mod, "get_mcp_client_manager", lambda: manager)


async def _topic_id() -> int:
    await init_db()
    async with SessionLocal() as session:
        topic = Topic(title="thinking", slug=f"thinking-{uuid4().hex[:8]}")
        session.add(topic)
        await session.commit()
        return topic.id


async def test_stream_emits_thinking_and_stores_it_per_round(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_empty_manager(monkeypatch)
    topic_id = await _topic_id()
    provider = _ThinksThenAnswers()

    events = [
        ev
        async for ev in turn_engine_mod.run_message_stream(
            kind="topic",
            container_id=topic_id,
            system_prompt="sys",
            history=[ChatMessage(role="user", content="question")],
            user_echo={"id": 1, "content": "question"},
            model="m",
            reasoning_effort="medium",
            max_tool_rounds=3,
            max_input_tokens=10_000,
            max_tool_result_tokens=1_000,
            provider=provider,
            github_token="",
            enabled_servers=[],
        )
    ]

    kinds = [e["event"] for e in events]
    reasoning = [json.loads(e["data"])["content"] for e in events if e["event"] == "reasoning"]
    assert reasoning == ["I need to look it up.", "**Answering**", "\n\nKeep it short."]
    # Each round's thinking streams before what that round produced.
    assert kinds.index("reasoning") < kinds.index("tool_calls")
    assert len(kinds) - 1 - kinds[::-1].index("reasoning") < kinds.index("delta")

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Message).where(Message.topic_id == topic_id).order_by(Message.id)
                )
            )
            .scalars()
            .all()
        )
    assistants = [m for m in rows if m.role == MessageRole.ASSISTANT]
    assert [m.reasoning for m in assistants] == [
        "I need to look it up.",
        "**Answering**\n\nKeep it short.",
    ]
    assert assistants[-1].content == "Here you go."

    # Display-only: the second round's prompt must not carry the first's thinking.
    replayed = "\n".join(m.content for m in provider.seen[1])
    assert "look it up" not in replayed


async def test_a_turn_without_thinking_stores_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_empty_manager(monkeypatch)
    topic_id = await _topic_id()

    class _Plain:
        name = "fake"

        async def stream_chat_with_tools(self, *, model, messages, tools, reasoning_effort=None):  # type: ignore[no-untyped-def]
            _ = model, messages, tools, reasoning_effort
            yield TextDeltaEvent(content="plain")
            yield TurnDoneEvent(finish_reason="stop")

    events = [
        ev
        async for ev in turn_engine_mod.run_message_stream(
            kind="topic",
            container_id=topic_id,
            system_prompt="sys",
            history=[],
            user_echo={"id": 1, "content": "q"},
            model="m",
            reasoning_effort="",
            max_tool_rounds=2,
            max_input_tokens=10_000,
            max_tool_result_tokens=1_000,
            provider=_Plain(),
            github_token="",
            enabled_servers=[],
        )
    ]

    assert "reasoning" not in [e["event"] for e in events]
    async with SessionLocal() as session:
        answer = (
            await session.execute(
                select(Message).where(
                    Message.topic_id == topic_id, Message.role == MessageRole.ASSISTANT
                )
            )
        ).scalar_one()
    assert answer.reasoning is None
