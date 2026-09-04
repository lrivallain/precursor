"""Workflow run progress — what the gallery draws its bars from.

``GET /api/workflows`` returns definitions, not traces, so a card used to have to
infer progress from each step agent's *current* status — which survives between
runs. ``run_progress`` folds the newest run's real advancement into the list
payload instead, so several live pipelines can be followed at once from the
Workflows home page without fetching a run per card.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import Workflow, WorkflowRun, WorkflowRunStep, WorkflowStep

_created: list[int] = []


@pytest.fixture(autouse=True)
async def _cleanup_workflows():
    """Drop the workflows a test created — the suite shares one SQLite file.

    Runs are removed explicitly: SQLite runs with foreign keys off here, so the
    ``ON DELETE CASCADE`` never fires and orphaned rows would be inherited by the
    next workflow to reuse the id.
    """
    yield
    async with SessionLocal() as session:
        for workflow_id in _created:
            run_ids = (
                (
                    await session.execute(
                        select(WorkflowRun.id).where(WorkflowRun.workflow_id == workflow_id)
                    )
                )
                .scalars()
                .all()
            )
            if run_ids:
                await session.execute(
                    delete(WorkflowRunStep).where(WorkflowRunStep.run_id.in_(run_ids))
                )
                await session.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(run_ids)))
            workflow = await session.get(Workflow, workflow_id)
            if workflow is not None:
                await session.delete(workflow)
        await session.commit()
    _created.clear()


async def _make_workflow(steps: int = 3, name: str = "progress pipeline") -> int:
    """A workflow of ``steps`` approval checkpoints — no agent rows required."""
    # Lifespan runs init_db (alembic upgrade head) before we touch the tables.
    with TestClient(create_app()):
        pass
    async with SessionLocal() as session:
        workflow = Workflow(name=name, status="running")
        session.add(workflow)
        await session.flush()
        for position in range(steps):
            session.add(WorkflowStep(workflow_id=workflow.id, position=position, kind="approval"))
        await session.commit()
        _created.append(workflow.id)
        return workflow.id


async def _add_run(
    workflow_id: int,
    run_number: int,
    status: str,
    attempts: list[tuple[int, str]],
    *,
    replays: list[tuple[int, str]] | None = None,
) -> int:
    """Record a run whose non-replay attempts are ``(position, status)`` in order."""
    async with SessionLocal() as session:
        run = WorkflowRun(
            workflow_id=workflow_id, run_number=run_number, status=status, trigger="manual"
        )
        session.add(run)
        await session.flush()
        for position, attempt_status in attempts:
            session.add(
                WorkflowRunStep(
                    run_id=run.id,
                    position=position,
                    kind="approval",
                    label=f"Step {position + 1}",
                    status=attempt_status,
                )
            )
        for position, attempt_status in replays or []:
            session.add(
                WorkflowRunStep(
                    run_id=run.id,
                    position=position,
                    kind="approval",
                    label=f"Step {position + 1}",
                    status=attempt_status,
                    replay=True,
                )
            )
        await session.commit()
        return run.id


def _progress(client: TestClient, workflow_id: int) -> dict | None:
    # Fetched by id rather than through the list: the suite shares one SQLite
    # file, and reading every workflow would couple these assertions to rows
    # other tests left behind. Both endpoints serialise through the same
    # ``_read``, so this exercises the identical code path.
    got = client.get(f"/api/workflows/{workflow_id}")
    assert got.status_code == 200
    return got.json()["run_progress"]


async def test_never_run_workflow_reports_no_progress() -> None:
    """A draft has no trace to read, so the card simply draws no bar."""
    workflow_id = await _make_workflow()
    with TestClient(create_app()) as client:
        assert _progress(client, workflow_id) is None


async def test_mid_run_reports_done_steps_and_the_live_cursor() -> None:
    workflow_id = await _make_workflow(steps=3)
    await _add_run(workflow_id, 1, "running", [(0, "completed"), (1, "running")])

    with TestClient(create_app()) as client:
        progress = _progress(client, workflow_id)

    assert progress is not None
    assert progress["status"] == "running"
    assert progress["run_number"] == 1
    assert (progress["done"], progress["total"]) == (1, 3)
    assert progress["current_position"] == 1


async def test_gate_loop_back_and_replays_do_not_inflate_progress() -> None:
    """Progress measures how far the *pipeline* got, not how many attempts ran."""
    workflow_id = await _make_workflow(steps=3)
    await _add_run(
        workflow_id,
        1,
        "running",
        # A gate at position 1 failed and sent position 0 back for a second pass.
        [(0, "completed"), (1, "failed"), (0, "completed"), (1, "running")],
        # An operator re-ran a finished step out of band; it advanced nothing.
        replays=[(2, "completed")],
    )

    with TestClient(create_app()) as client:
        progress = _progress(client, workflow_id)

    assert progress is not None
    # Four attempts, one replay — but only position 0 ever actually resolved.
    assert (progress["done"], progress["total"]) == (1, 3)
    assert progress["current_position"] == 1


async def test_only_the_newest_run_is_reported() -> None:
    """Older executions belong to the board's trace timeline, not the card."""
    workflow_id = await _make_workflow(steps=2)
    await _add_run(workflow_id, 1, "completed", [(0, "completed"), (1, "completed")])
    await _add_run(workflow_id, 2, "running", [(0, "running")])

    with TestClient(create_app()) as client:
        progress = _progress(client, workflow_id)

    assert progress is not None
    assert progress["run_number"] == 2
    assert (progress["done"], progress["total"]) == (0, 2)
    assert progress["current_position"] == 0


async def test_progress_never_reads_past_full_after_a_step_is_removed() -> None:
    """A run recorded against a longer definition must not overflow the bar."""
    workflow_id = await _make_workflow(steps=1)
    await _add_run(workflow_id, 1, "completed", [(0, "completed"), (1, "completed"), (2, "passed")])

    with TestClient(create_app()) as client:
        progress = _progress(client, workflow_id)

    assert progress is not None
    assert (progress["done"], progress["total"]) == (1, 1)


async def test_progress_is_resolved_per_workflow_in_one_pass() -> None:
    """What lets the gallery follow several pipelines at once off a single list.

    Exercises the batched read directly: the whole point is that N workflows cost
    one pair of queries, and each still gets *its own* run rather than the
    newest one in the table.
    """
    from precursor.backend.routers.workflows import _read

    ahead = await _make_workflow(steps=4, name="ahead")
    behind = await _make_workflow(steps=4, name="behind")
    await _add_run(ahead, 1, "running", [(0, "completed"), (1, "completed"), (2, "running")])
    await _add_run(behind, 1, "running", [(0, "running")])

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Workflow)
                    .where(Workflow.id.in_([ahead, behind]))
                    .options(selectinload(Workflow.steps))
                )
            )
            .scalars()
            .all()
        )
        reads = await _read(session, list(rows))

    by_id = {r.id: r.run_progress for r in reads}
    assert by_id[ahead] is not None and by_id[behind] is not None
    assert (by_id[ahead].done, by_id[ahead].current_position) == (2, 2)
    assert (by_id[behind].done, by_id[behind].current_position) == (0, 0)
