"""Agents stream their thinking live and archive only events with content.

The SDK streams a round as frames — thinking and answer deltas, byte counters,
partial tool arguments — then sends the complete message, reasoning and tool
events. The frames used to be archived one row (and one commit) each, with their
text dropped on the way, until they were ~89% of ``agent_events``; meanwhile no
view could show the model thinking while it thought. Now the frames feed a
per-run live stream and are never archived. Covers: delta text extraction, the
content-free predicate, the live stream's thinking state, the archive filter
end to end, rehydration after a restart with legacy rows in the archive, and
the storage cleanup target that clears them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import AgentEventRecord, AgentRun, AgentSession
from precursor.backend.schemas.agent import AgentEvent, AgentLiveThinking
from precursor.backend.services.agents import manager as manager_mod
from precursor.backend.services.agents.event_normalizer import is_content_free, normalize_event
from precursor.backend.services.agents.live_session import _LiveSession
from precursor.backend.services.agents.live_stream import LiveStream
from precursor.backend.services.agents.manager import AgentManager
from precursor.backend.services.empty_agent_events import prune_empty_agent_events

# -- SDK stand-ins, named as the SDK names them: the normaliser and the manager
# dispatch on the class name.


class AssistantTurnStartData:
    pass


class AssistantTurnEndData:
    pass


class AssistantStreamingDeltaData:
    def __init__(self, size: int = 10) -> None:
        self.total_response_size_bytes = size


class AssistantReasoningDeltaData:
    def __init__(self, delta: str) -> None:
        self.delta_content = delta
        self.reasoning_id = "r1"


class AssistantReasoningData:
    def __init__(self, content: str) -> None:
        self.content = content
        self.reasoning_id = "r1"


class AssistantMessageStartData:
    def __init__(self) -> None:
        self.message_id = "m1"


class AssistantMessageDeltaData:
    def __init__(self, delta: str) -> None:
        self.delta_content = delta
        self.message_id = "m1"


class AssistantMessageData:
    def __init__(self, content: str) -> None:
        self.content = content
        self.message_id = "m1"


class AssistantToolCallDeltaData:
    def __init__(self, delta: str) -> None:
        self.input_delta = delta
        self.tool_call_id = "call-1"
        self.tool_name = "bash"


def _ev(kind: str, text: str | None = None) -> AgentEvent:
    return AgentEvent(kind=kind, text=text)


# -- Normalising --------------------------------------------------------------


def test_normalize_reads_streaming_delta_text() -> None:
    """Deltas carry their text in ``delta_content`` / ``input_delta``, which the
    normaliser used to miss: every ``assistant_delta`` archived ``text: null``."""
    thinking = normalize_event(AssistantReasoningDeltaData("Let me check"))
    answer = normalize_event(AssistantMessageDeltaData("Here is"))
    tool = normalize_event(AssistantToolCallDeltaData('{"command": "ls'))

    assert (thinking.kind, thinking.text) == ("reasoning_delta", "Let me check")
    assert (answer.kind, answer.text) == ("assistant_delta", "Here is")
    assert tool.text == '{"command": "ls'
    assert tool.tool_name == "bash"


def test_content_free_covers_frames_and_textless_messages() -> None:
    for kind in (
        "assistant_delta",
        "reasoning_delta",
        "AssistantStreamingDeltaData",
        "AssistantToolCallDeltaData",
        "AssistantMessageStartData",
    ):
        # Frames are live-only even when they carry text.
        assert is_content_free(kind, "some text")
    # A tool-call-only round's message, and a model's encrypted reasoning.
    assert is_content_free("assistant_message", None)
    assert is_content_free("reasoning", "  \n")
    assert not is_content_free("assistant_message", "Done.")
    assert not is_content_free("reasoning", "Checking the config.")
    # Everything else is kept whether or not it has text.
    assert not is_content_free("turn_end", None)
    assert not is_content_free("ToolExecutionStartData", None)


# -- The live stream ----------------------------------------------------------


def test_live_stream_follows_the_thinking_until_its_complete_event() -> None:
    stream = LiveStream()

    assert stream.observe(_ev("reasoning_delta", "**Reading**\n")).changed
    stream.observe(_ev("AssistantStreamingDeltaData"))
    assert stream.observe(_ev("reasoning_delta", "The config first.")).changed
    assert stream.thinking == "**Reading**\nThe config first."
    assert not stream.answering

    # The model moves on to a tool call: done thinking, though the complete
    # reasoning event hasn't landed yet. The text stays for the timeline.
    assert stream.observe(_ev("AssistantToolCallDeltaData", "{")).changed
    assert stream.thinking is None
    assert stream.answering
    assert stream.reasoning == "**Reading**\nThe config first."

    stream.observe(_ev("assistant_message", None))
    update = stream.observe(_ev("reasoning", "**Reading**\nThe config first."))
    # The complete event carries the thinking; nothing to recover.
    assert update.flush is None
    assert stream.reasoning == ""


def test_live_stream_recovers_thinking_its_complete_event_lost() -> None:
    stream = LiveStream()
    stream.observe(_ev("reasoning_delta", "Streamed thinking."))
    update = stream.observe(_ev("reasoning", ""))
    assert update.flush is not None
    assert (update.flush.kind, update.flush.text) == ("reasoning", "Streamed thinking.")

    # A stopped turn keeps what the model had thought so far.
    stream.observe(_ev("reasoning_delta", "Half a thought"))
    stream.observe(_ev("assistant_delta", "Ans"))
    update = stream.observe(_ev("aborted"))
    assert update.flush is not None
    assert update.flush.text == "Half a thought"
    assert stream.message == ""
    assert stream.reasoning == ""


# -- End to end through the manager -------------------------------------------

_created: list[int] = []


@pytest.fixture(autouse=True)
async def _drop_created_agents() -> Any:
    """The scratch DB is shared across the suite: leave no agent behind."""
    yield
    if not _created:
        return
    ids = list(_created)
    _created.clear()
    async with SessionLocal() as session:
        await session.execute(
            update(AgentSession).where(AgentSession.id.in_(ids)).values(current_run_id=None)
        )
        await session.execute(
            delete(AgentEventRecord).where(AgentEventRecord.agent_session_id.in_(ids))
        )
        await session.execute(delete(AgentRun).where(AgentRun.agent_id.in_(ids)))
        await session.execute(delete(AgentSession).where(AgentSession.id.in_(ids)))
        await session.commit()


async def _make_running_agent() -> tuple[int, int]:
    async with SessionLocal() as session:
        agent = AgentSession(title="Thinker", task_prompt="think", status="running")
        session.add(agent)
        await session.flush()
        run = AgentRun(agent_id=agent.id, trigger="manual", status="running")
        session.add(run)
        await session.flush()
        agent.current_run_id = run.id
        await session.commit()
        _created.append(agent.id)
        return agent.id, run.id


def _manager_for(agent_id: int, run_id: int) -> AgentManager:
    mgr = AgentManager()
    mgr._advance_workflows = lambda rid: None  # type: ignore[method-assign]
    mgr.enqueue = lambda coro: None  # type: ignore[method-assign]
    mgr._live[run_id] = _LiveSession(sdk_session=object())
    mgr._agent_runs[agent_id] = run_id
    return mgr


async def _archived(agent_id: int) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(AgentEventRecord.payload)
            .where(AgentEventRecord.agent_session_id == agent_id)
            .order_by(AgentEventRecord.id)
        )
        return [json.loads(p) for p in rows.scalars()]


async def test_streaming_frames_feed_the_live_view_but_are_never_archived(
    monkeypatch: Any,
) -> None:
    await init_db()
    agent_id, run_id = await _make_running_agent()
    mgr = _manager_for(agent_id, run_id)
    published: list[int | None] = []

    async def _publish(**kwargs: Any) -> None:
        published.append(kwargs.get("agent_run_id"))

    monkeypatch.setattr(manager_mod, "publish_agent_changed", _publish)

    async def feed(*events: object) -> None:
        for event in events:
            await mgr._handle_event_locked(run_id, event)

    # A round as Claude streams it: thinking, then a tool call.
    await feed(
        AssistantTurnStartData(),
        AssistantStreamingDeltaData(),
        AssistantReasoningDeltaData("**Checking the config**\n"),
        AssistantStreamingDeltaData(),
        AssistantReasoningDeltaData("It lives under agents."),
    )
    page = await mgr.get_events_page(agent_id)
    # While it thinks, the thinking travels outside the cursor, live.
    assert page.thinking == AgentLiveThinking(
        text="**Checking the config**\nIt lives under agents.", active=True
    )
    assert [e.kind for e in page.events] == ["turn_start"]
    assert mgr.live_activity([agent_id])[agent_id]["active_thinking"] is not None
    # The first frame of thinking reached open views without waiting for the round.
    assert run_id in published

    await feed(
        AssistantToolCallDeltaData("{"),
        AssistantMessageStartData(),
        AssistantMessageData(""),  # tool-call-only round: no text
        AssistantReasoningData("**Checking the config**\nIt lives under agents."),
        AssistantTurnEndData(),
        # The answering round, from a model whose reasoning is encrypted.
        AssistantTurnStartData(),
        AssistantMessageStartData(),
        AssistantMessageDeltaData("It is "),
        AssistantMessageDeltaData("under agents."),
        AssistantMessageData("It is under agents."),
        AssistantReasoningData(""),
        AssistantTurnEndData(),
    )

    archived = await _archived(agent_id)
    assert [(e["kind"], e.get("text")) for e in archived] == [
        ("turn_start", None),
        ("reasoning", "**Checking the config**\nIt lives under agents."),
        ("turn_end", None),
        ("turn_start", None),
        ("assistant_message", "It is under agents."),
        ("turn_end", None),
    ]
    # The in-memory timeline holds exactly what was archived, and nothing is
    # left streaming once the rounds are over.
    page = await mgr.get_events_page(agent_id)
    assert [(e.kind, e.text) for e in page.events] == [(e["kind"], e.get("text")) for e in archived]
    assert page.thinking is None
    assert mgr.live_activity([agent_id])[agent_id]["active_thinking"] is None


async def test_rehydration_after_a_restart_matches_the_live_timeline() -> None:
    """A restart rebuilds the timeline from the archive, legacy frames skipped.

    Rows archived before frames stopped being kept are still in the table; left
    in, they'd make the rebuilt transcript longer than the one a live reader's
    cursor was taken against.
    """
    await init_db()
    agent_id, run_id = await _make_running_agent()
    mgr = _manager_for(agent_id, run_id)
    await mgr._handle_event_locked(run_id, AssistantTurnStartData())
    # Legacy rows, as the archive held them before this change.
    async with SessionLocal() as session:
        for payload in (
            {"kind": "assistant_delta", "text": None},
            {"kind": "AssistantStreamingDeltaData", "text": None},
            {"kind": "reasoning", "text": None},
            {"kind": "assistant_message", "text": None},
        ):
            session.add(
                AgentEventRecord(
                    agent_session_id=agent_id, agent_run_id=run_id, payload=json.dumps(payload)
                )
            )
        await session.commit()
    for event in (
        AssistantReasoningDeltaData("Thinking it over."),
        AssistantMessageData("Done."),
        AssistantReasoningData("Thinking it over."),
        AssistantTurnEndData(),
    ):
        await mgr._handle_event_locked(run_id, event)
    before = await mgr.get_events_page(agent_id)

    restarted = AgentManager()
    after = await restarted.get_events_page(agent_id)

    assert [(e.kind, e.text) for e in after.events] == [(e.kind, e.text) for e in before.events]
    assert [e.kind for e in after.events] == [
        "turn_start",
        "assistant_message",
        "reasoning",
        "turn_end",
    ]
    assert after.cursor == before.cursor
    # A cursor taken before the restart still addresses the same transcript.
    resumed = await restarted.get_events_page(agent_id, after=before.cursor)
    assert not resumed.reset
    assert resumed.events == []


# -- Cleanup ------------------------------------------------------------------


async def test_empty_events_cleanup_removes_only_content_free_rows() -> None:
    await init_db()
    async with SessionLocal() as session:
        await session.execute(delete(AgentEventRecord))
        agent = AgentSession(title="Old", task_prompt="x", status="completed")
        session.add(agent)
        await session.flush()
        _created.append(agent.id)
        payloads = [
            # Content-free: compact as archived, and spaced as a recap rewrites.
            '{"kind":"assistant_delta","text":null}',
            '{"kind":"AssistantStreamingDeltaData","text":null}',
            '{"kind": "AssistantToolCallDeltaData", "text": null, "tool_name": "bash"}',
            '{"kind":"AssistantMessageStartData","text":null}',
            '{"kind":"reasoning_delta","text":null}',
            '{"kind":"assistant_message","text":null}',
            '{"kind":"reasoning","text":""}',
            # Kept: every one of these renders.
            '{"kind":"assistant_message","text":"The answer."}',
            '{"kind":"reasoning","text":"A thought."}',
            '{"kind":"turn_end","text":null}',
            '{"kind":"ToolExecutionStartData","text":null,"tool_name":"assistant_delta"}',
        ]
        for payload in payloads:
            session.add(AgentEventRecord(agent_session_id=agent.id, payload=payload))
        await session.commit()
        agent_id = agent.id

    preview = await prune_empty_agent_events(dry_run=True)
    assert preview.rows == 7
    assert preview.bytes == sum(len(p) for p in payloads[:7])
    assert len(await _archived(agent_id)) == len(payloads)

    result = await prune_empty_agent_events()
    assert result.rows == 7
    assert [e.get("text") or e["kind"] for e in await _archived(agent_id)] == [
        "The answer.",
        "A thought.",
        "turn_end",
        "ToolExecutionStartData",
    ]
    # Idempotent.
    assert (await prune_empty_agent_events()).rows == 0
