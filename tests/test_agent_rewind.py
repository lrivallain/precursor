"""Rewinding an agent session to an earlier prompt (issue #401).

The conversation lives in the Copilot SDK session and Precursor archives a
normalised copy, so a rewind must cut both in step. The SDK's experimental
``rpc.history`` RPCs are mocked; the archive, run state and artifacts are real.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import AgentEventRecord, AgentRun, AgentSession
from precursor.backend.models.agent_artifact import AgentArtifact
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents import rewind as rewind_mod
from precursor.backend.services.agents.event_normalizer import normalize_event
from precursor.backend.services.agents.live_session import _LiveSession
from precursor.backend.services.agents.manager import AgentManager
from precursor.backend.services.agents.rewind import RewindError

_created: list[int] = []


@pytest.fixture(autouse=True)
async def _drop_created_agents():
    yield
    if not _created:
        return
    ids = list(_created)
    _created.clear()
    async with SessionLocal() as session:
        await session.execute(
            update(AgentSession).where(AgentSession.id.in_(ids)).values(current_run_id=None)
        )
        await session.execute(delete(AgentArtifact).where(AgentArtifact.agent_id.in_(ids)))
        await session.execute(
            delete(AgentEventRecord).where(AgentEventRecord.agent_session_id.in_(ids))
        )
        await session.execute(delete(AgentRun).where(AgentRun.agent_id.in_(ids)))
        await session.execute(delete(AgentSession).where(AgentSession.id.in_(ids)))
        await session.commit()


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: Any) -> None:
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(rewind_mod.asyncio, "sleep", _sleep)


class _History:
    """``rpc.history`` stand-in recording the rewind calls."""

    def __init__(self, points: list[str], outcomes: list[str] | None = None) -> None:
        self.points = points
        self.outcomes = list(outcomes or ["success"])
        self.calls: list[Any] = []

    async def list_rewind_points(self, *, timeout: float | None = None) -> Any:
        return SimpleNamespace(
            points=[SimpleNamespace(event_id=p) for p in self.points],
            unavailable_reason=None,
            file_change_tracking_enabled=False,
        )

    async def rewind(self, params: Any, *, timeout: float | None = None) -> Any:
        self.calls.append(params)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        return SimpleNamespace(
            outcome=outcome,
            error="disk full" if outcome == "truncation-failed" else None,
            events_removed=7 if outcome == "success" else None,
            restored_files=[],
            skipped_files=[],
        )


async def _make_agent(status: str = "idle") -> tuple[int, int]:
    await init_db()
    async with SessionLocal() as session:
        agent = AgentSession(title="Rewinder", task_prompt="plan", status=status)
        session.add(agent)
        await session.flush()
        run = AgentRun(agent_id=agent.id, trigger="manual", status=status, copilot_session_id="sid")
        session.add(run)
        await session.flush()
        agent.current_run_id = run.id
        await session.commit()
        _created.append(agent.id)
        return agent.id, run.id


def _manager(run_id: int, agent_id: int, history: _History) -> AgentManager:
    mgr = AgentManager()
    live = _LiveSession(sdk_session=SimpleNamespace(rpc=SimpleNamespace(history=history)))
    mgr._live[run_id] = live
    mgr._agent_runs[agent_id] = run_id

    async def _publish(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _ensure_live(_agent: Any, _run: Any) -> _LiveSession:
        return live

    mgr._publish = _publish  # type: ignore[method-assign]
    mgr._ensure_live = _ensure_live  # type: ignore[method-assign]
    return mgr


async def _seed_two_turns(mgr: AgentManager, agent_id: int, run_id: int) -> tuple[str, str]:
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    t0 = datetime.now(UTC) - timedelta(minutes=5)
    turns = [
        AgentEvent(kind="UserMessageData", text="draft a plan", event_id=first, at=t0),
        AgentEvent(kind="assistant_message", text="Plan v1", at=t0 + timedelta(seconds=5)),
        AgentEvent(
            kind="UserMessageData",
            text="now rewrite it",
            event_id=second,
            at=t0 + timedelta(minutes=1),
        ),
        AgentEvent(kind="tool_call", tool_name="edit", at=t0 + timedelta(minutes=1, seconds=2)),
        AgentEvent(kind="assistant_message", text="Plan v2", at=t0 + timedelta(minutes=2)),
    ]
    for ev in turns:
        ev.agent_run_id = run_id
        await mgr._record(agent_id, ev)
    mgr._loaded.add(agent_id)
    async with SessionLocal() as session:
        session.add_all(
            [
                AgentArtifact(
                    agent_id=agent_id,
                    agent_run_id=run_id,
                    key="output",
                    kind="text",
                    title="v1",
                    content="Plan v1",
                    created_at=t0 + timedelta(seconds=6),
                ),
                AgentArtifact(
                    agent_id=agent_id,
                    agent_run_id=run_id,
                    key="output",
                    kind="text",
                    title="v2",
                    content="Plan v2",
                    created_at=t0 + timedelta(minutes=2),
                ),
            ]
        )
        await session.commit()
    return first, second


async def _archived_texts(agent_id: int) -> list[str | None]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(AgentEventRecord.payload)
            .where(AgentEventRecord.agent_session_id == agent_id)
            .order_by(AgentEventRecord.id)
        )
        return [json.loads(p)["text"] for p in rows.scalars()]


def test_prompt_events_carry_their_sdk_id() -> None:
    from copilot.generated.session_events import UserMessageData

    sdk_id = uuid.uuid4()
    envelope = SimpleNamespace(id=sdk_id, data=UserMessageData(content="hello"))
    assert normalize_event(envelope).event_id == str(sdk_id)

    class AssistantMessageData:
        content = "hi"

    answer = SimpleNamespace(id=uuid.uuid4(), data=AssistantMessageData())
    assert normalize_event(answer).event_id is None


async def test_rewind_cuts_the_sdk_history_and_the_archive_together() -> None:
    agent_id, run_id = await _make_agent()
    history = _History(points=[], outcomes=["session-busy", "success"])
    mgr = _manager(run_id, agent_id, history)
    first, second = await _seed_two_turns(mgr, agent_id, run_id)
    history.points = [first, second]
    await mgr._patch_run(run_id, status="completed", result_summary="Plan v2", progress=100)

    before = await mgr.get_events_page(agent_id)
    result = await mgr.rewind(agent_id, second)

    # The SDK is asked to drop the second prompt onward, conversation only, and
    # a transient session-busy is retried rather than surfaced.
    assert [c.event_id for c in history.calls] == [second, second]
    assert history.calls[-1].mode.value == "conversation"
    assert result.events_removed == 3
    assert result.sdk_events_removed == 7

    assert await _archived_texts(agent_id) == ["draft a plan", "Plan v1"]
    assert [e.text for e in mgr._events[agent_id]] == ["draft a plan", "Plan v1"]

    # A live reader's cursor no longer addresses the archive, even once it grows
    # back past it: the changed epoch resets it.
    for text in ("redo", "Plan v3", "more", "Plan v4"):
        await mgr._record(
            agent_id, AgentEvent(kind="assistant_message", text=text, agent_run_id=run_id)
        )
    page = await mgr.get_events_page(agent_id, after=before.cursor, epoch=before.epoch)
    assert page.reset is True
    assert [e.text for e in page.events][:2] == ["draft a plan", "Plan v1"]

    run = await mgr._run(run_id)
    assert run is not None
    assert run.status == "idle"
    assert run.result_summary == "Plan v1"
    assert run.progress is None

    async with SessionLocal() as session:
        titles = (
            (
                await session.execute(
                    select(AgentArtifact.title).where(AgentArtifact.agent_run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
    assert titles == ["v1"]


async def test_rewind_is_refused_while_the_agent_works() -> None:
    agent_id, run_id = await _make_agent(status="running")
    history = _History(points=[])
    mgr = _manager(run_id, agent_id, history)
    _first, second = await _seed_two_turns(mgr, agent_id, run_id)

    with pytest.raises(RewindError, match="busy"):
        await mgr.rewind(agent_id, second)
    assert history.calls == []


async def test_only_the_current_runs_prompts_can_be_rewound() -> None:
    agent_id, run_id = await _make_agent()
    history = _History(points=[])
    mgr = _manager(run_id, agent_id, history)
    older = str(uuid.uuid4())
    await mgr._record(
        agent_id,
        AgentEvent(kind="UserMessageData", text="old run", event_id=older, agent_run_id=None),
    )
    mgr._loaded.add(agent_id)

    with pytest.raises(RewindError, match="current run") as err:
        await mgr.rewind(agent_id, older)
    assert err.value.status_code == 400
    with pytest.raises(RewindError) as bad:
        await mgr.rewind(agent_id, "not-a-uuid")
    assert bad.value.status_code == 400


async def test_a_turn_the_sdk_no_longer_lists_is_refused() -> None:
    agent_id, run_id = await _make_agent()
    history = _History(points=[])
    mgr = _manager(run_id, agent_id, history)
    first, second = await _seed_two_turns(mgr, agent_id, run_id)
    history.points = [first]

    with pytest.raises(RewindError, match="compacted"):
        await mgr.rewind(agent_id, second)
    assert history.calls == []
    assert len(await _archived_texts(agent_id)) == 5


async def test_a_failed_sdk_rewind_leaves_the_transcript_alone() -> None:
    agent_id, run_id = await _make_agent()
    history = _History(points=[], outcomes=["truncation-failed"])
    mgr = _manager(run_id, agent_id, history)
    first, second = await _seed_two_turns(mgr, agent_id, run_id)
    history.points = [first, second]
    epoch = mgr._transcript.epoch(agent_id)

    with pytest.raises(RewindError, match="disk full") as err:
        await mgr.rewind(agent_id, second)
    assert err.value.status_code == 502
    assert len(await _archived_texts(agent_id)) == 5
    assert len(mgr._events[agent_id]) == 5
    assert mgr._transcript.epoch(agent_id) == epoch


async def test_a_session_that_stays_busy_reports_it() -> None:
    agent_id, run_id = await _make_agent()
    history = _History(points=[], outcomes=["session-busy"])
    mgr = _manager(run_id, agent_id, history)
    first, second = await _seed_two_turns(mgr, agent_id, run_id)
    history.points = [first, second]

    with pytest.raises(RewindError, match="settling"):
        await mgr.rewind(agent_id, second)
    assert len(history.calls) == rewind_mod._BUSY_RETRIES
    assert len(await _archived_texts(agent_id)) == 5


async def test_rewind_endpoint_maps_refusals_to_http(monkeypatch: Any) -> None:
    from precursor.backend.main import create_app
    from precursor.backend.routers import agents as agents_router

    agent_id, _run_id = await _make_agent()

    async def _runtime_ok(_session: Any) -> None:
        return None

    class _Mgr:
        async def rewind(self, _agent_id: int, _event_id: str) -> Any:
            raise RewindError("The agent is busy.", status_code=409)

    monkeypatch.setattr(agents_router, "_require_runtime", _runtime_ok)
    monkeypatch.setattr(agents_router, "get_agent_manager", lambda: _Mgr())

    with TestClient(create_app()) as client:
        resp = client.post(f"/api/agents/{agent_id}/rewind", json={"event_id": str(uuid.uuid4())})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "The agent is busy."
        assert client.post(f"/api/agents/{agent_id}/rewind", json={}).status_code == 422


async def test_a_turn_started_during_the_rewind_keeps_its_status() -> None:
    agent_id, run_id = await _make_agent()
    history = _History(points=[])
    mgr = _manager(run_id, agent_id, history)
    first, second = await _seed_two_turns(mgr, agent_id, run_id)
    history.points = [first, second]
    sdk_rewind = history.rewind

    # A send lands right after the SDK cut, before Precursor resets the run.
    async def _rewind_then_send(params: Any, *, timeout: float | None = None) -> Any:
        result = await sdk_rewind(params, timeout=timeout)
        await mgr._patch_run(run_id, status="running", active_prompt="next please")
        return result

    history.rewind = _rewind_then_send  # type: ignore[method-assign]
    await mgr.rewind(agent_id, second)

    run = await mgr._run(run_id)
    assert run is not None
    assert run.status == "running"
    assert run.active_prompt == "next please"
    assert run.result_summary == "Plan v1"
