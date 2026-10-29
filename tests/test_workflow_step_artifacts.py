"""Workflow steps publish their artifacts even when they aren't autonomous (#388).

Every task step is told to publish its deliverable with an ``ARTIFACT`` block,
but directive parsing was gated on ``autonomy_enabled`` — which a workflow step
usually doesn't have. The blocks were silently dropped, so the blackboard that
``auto`` and ``selected`` context modes read from earlier steps stayed empty.
Only the terminal directives belong to the autonomy loop; progress and artifacts
are how any step reports and publishes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from precursor.backend.db import SessionLocal, init_db
from precursor.backend.models import AgentArtifact, AgentEventRecord, AgentRun, AgentSession
from precursor.backend.models.workflow import Workflow, WorkflowRun
from precursor.backend.services.agents.directives import strip_published_artifacts
from precursor.backend.services.agents.live_session import _LiveSession
from precursor.backend.services.agents.manager import AgentManager
from precursor.backend.services.agents.workflow import (
    collect_prior_artifacts,
    collect_step_context,
)

_agents: list[int] = []
_workflows: list[int] = []

_STEP_ANSWER = (
    "Two notices were cancelled and neither was still on the calendar.\n"
    "PROGRESS: 80 | matched the notices against the calendar\n"
    "\n"
    "ARTIFACT: Cancellations\n"
    '[{"messageId": "AAMk-1", "action": "deleted"}]\n'
    "END_ARTIFACT\n"
    "\n"
    "OBJECTIVE_COMPLETE: I found 2 cancellation notices in the inbox."
)


@pytest.fixture(autouse=True)
async def _cleanup() -> Any:
    """The scratch DB is shared across the suite: leave no agent or workflow behind."""
    await init_db()
    yield
    async with SessionLocal() as session:
        if _agents:
            await session.execute(
                update(AgentSession).where(AgentSession.id.in_(_agents)).values(current_run_id=None)
            )
            await session.execute(delete(AgentArtifact).where(AgentArtifact.agent_id.in_(_agents)))
            await session.execute(
                delete(AgentEventRecord).where(AgentEventRecord.agent_session_id.in_(_agents))
            )
            await session.execute(delete(AgentRun).where(AgentRun.agent_id.in_(_agents)))
            await session.execute(delete(AgentSession).where(AgentSession.id.in_(_agents)))
        if _workflows:
            await session.execute(
                delete(WorkflowRun).where(WorkflowRun.workflow_id.in_(_workflows))
            )
            await session.execute(delete(Workflow).where(Workflow.id.in_(_workflows)))
        await session.commit()
    _agents.clear()
    _workflows.clear()


async def _seed(*, in_workflow: bool, autonomy: bool = False) -> tuple[int, int, int | None]:
    """An agent with a running execution, driven by a workflow run or not.

    Returns ``(agent_id, agent_run_id, workflow_run_id)``.
    """
    async with SessionLocal() as session:
        wf_run_id: int | None = None
        if in_workflow:
            wf = Workflow(name="Meeting triage", status="running")
            session.add(wf)
            await session.flush()
            _workflows.append(wf.id)
            wf_run = WorkflowRun(workflow_id=wf.id, run_number=1, status="running")
            session.add(wf_run)
            await session.flush()
            wf_run_id = wf_run.id
        agent = AgentSession(
            title="Find cancellations",
            task_prompt="find them",
            status="running",
            autonomy_enabled=autonomy,
        )
        session.add(agent)
        await session.flush()
        _agents.append(agent.id)
        run = AgentRun(
            agent_id=agent.id,
            trigger="workflow" if in_workflow else "manual",
            workflow_run_id=wf_run_id,
            status="running",
        )
        session.add(run)
        await session.flush()
        agent.current_run_id = run.id
        await session.commit()
        return agent.id, run.id, wf_run_id


async def _rest(mgr: AgentManager, agent_id: int, run_id: int) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    async with SessionLocal() as session:
        agent = await session.get(AgentSession, agent_id)
        run = await session.get(AgentRun, run_id)
        assert agent is not None and run is not None
        await mgr._on_idle(agent, run, patch)
    return patch


async def _artifacts(agent_id: int) -> list[AgentArtifact]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(AgentArtifact).where(AgentArtifact.agent_id == agent_id)
        )
        return list(rows.scalars().all())


async def test_plain_workflow_step_publishes_its_artifact() -> None:
    agent_id, run_id, wf_run_id = await _seed(in_workflow=True)
    mgr = AgentManager()
    mgr._live[run_id] = _LiveSession(sdk_session=None, pending_answer=_STEP_ANSWER)

    patch = await _rest(mgr, agent_id, run_id)

    arts = await _artifacts(agent_id)
    assert [(a.title, a.content, a.key, a.agent_run_id) for a in arts] == [
        ("Cancellations", '[{"messageId": "AAMk-1", "action": "deleted"}]', "output", run_id)
    ]
    assert patch["progress"] == 80
    # A later step's blackboard, scoped to this workflow run, now carries it.
    async with SessionLocal() as session:
        board = await collect_prior_artifacts(session, [agent_id], workflow_run_id=wf_run_id)
    assert "AAMk-1" in board


async def test_plain_workflow_step_still_rests_at_idle() -> None:
    """Terminal directives stay autonomy-only: the coordinator reads an idle step."""
    agent_id, run_id, _ = await _seed(in_workflow=True)
    mgr = AgentManager()
    mgr._live[run_id] = _LiveSession(
        sdk_session=None, pending_answer="NEED_INPUT: which calendar?\n" + _STEP_ANSWER
    )

    patch = await _rest(mgr, agent_id, run_id)

    assert patch["status"] == "idle"
    assert "blocked_question" not in patch
    assert "finished_at" not in patch
    # Only the model's artifact: no auto-captured "Result" from OBJECTIVE_COMPLETE.
    assert [a.key for a in await _artifacts(agent_id)] == ["output"]


async def test_trailing_idle_does_not_publish_twice() -> None:
    """Idle isn't sticky, so a background sub-agent settling re-enters ``_on_idle``."""
    agent_id, run_id, _ = await _seed(in_workflow=True)
    mgr = AgentManager()
    mgr._live[run_id] = _LiveSession(sdk_session=None, pending_answer=_STEP_ANSWER)

    await _rest(mgr, agent_id, run_id)
    await _rest(mgr, agent_id, run_id)

    assert len(await _artifacts(agent_id)) == 1


async def test_plain_agent_outside_a_workflow_ignores_the_protocol() -> None:
    """An agent never taught the directives mustn't publish by quoting one."""
    agent_id, run_id, _ = await _seed(in_workflow=False)
    mgr = AgentManager()
    mgr._live[run_id] = _LiveSession(sdk_session=None, pending_answer=_STEP_ANSWER)

    patch = await _rest(mgr, agent_id, run_id)

    assert await _artifacts(agent_id) == []
    assert "progress" not in patch
    assert patch["status"] == "idle"


async def test_handoff_forwards_a_published_artifact_once() -> None:
    agent_id, run_id, wf_run_id = await _seed(in_workflow=True)
    answer = _STEP_ANSWER.replace(
        "OBJECTIVE_COMPLETE", "ARTIFACT: Kept | never stored\nOBJECTIVE_COMPLETE"
    )
    async with SessionLocal() as session:
        session.add(
            AgentEventRecord(
                agent_session_id=agent_id,
                agent_run_id=run_id,
                payload=json.dumps({"kind": "assistant_message", "text": answer}),
            )
        )
        session.add(
            AgentArtifact(
                agent_id=agent_id,
                agent_run_id=run_id,
                key="output",
                title="Cancellations",
                content='[{"messageId": "AAMk-1", "action": "deleted"}]',
            )
        )
        await session.commit()
        context = await collect_step_context(session, agent_id, workflow_run_id=wf_run_id)

    assert context.count("AAMk-1") == 1
    assert "[Cancellations]" in context
    assert "ARTIFACT: Cancellations" not in context
    assert "Two notices were cancelled" in context
    # Not on the board, so the prose is its only copy and it stays.
    assert "ARTIFACT: Kept | never stored" in context


async def test_handoff_does_not_fall_back_to_the_summary_after_lifting() -> None:
    """A message that was only an artifact mustn't forward its body again."""
    agent_id, run_id, wf_run_id = await _seed(in_workflow=True)
    async with SessionLocal() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.result_summary = "the report body"
        session.add(
            AgentEventRecord(
                agent_session_id=agent_id,
                agent_run_id=run_id,
                payload=json.dumps(
                    {
                        "kind": "assistant_message",
                        "text": "ARTIFACT: Report\nthe report body\nEND_ARTIFACT\n"
                        "OBJECTIVE_COMPLETE: done",
                    }
                ),
            )
        )
        session.add(
            AgentArtifact(
                agent_id=agent_id,
                agent_run_id=run_id,
                key="output",
                title="Report",
                content="the report body",
            )
        )
        await session.commit()
        context = await collect_step_context(session, agent_id, workflow_run_id=wf_run_id)

    assert context.count("the report body") == 1


def test_strip_published_artifacts_only_lifts_exact_matches() -> None:
    text = "Intro.\nARTIFACT: A | one\nARTIFACT: B\ntwo\nEND_ARTIFACT\nOutro."

    assert strip_published_artifacts(text, set()) == text
    assert strip_published_artifacts(text, {("A", "changed")}) == text
    assert strip_published_artifacts(text, {("A", "one"), ("B", "two")}) == "Intro.\nOutro."
    assert strip_published_artifacts(text, {("B", "two")}) == "Intro.\nARTIFACT: A | one\nOutro."
