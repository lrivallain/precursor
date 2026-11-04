"""Retention of archived workflow step input context."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from precursor.backend.db import SessionLocal
from precursor.backend.main import create_app
from precursor.backend.models import AppSetting, Workflow, WorkflowRun, WorkflowRunStep
from precursor.backend.services.workflow_step_retention import (
    PRUNED_PLACEHOLDER,
    prune_expired_workflow_step_inputs,
)


async def _set(days: int) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, "workflow_step_retention_days")
        if row is None:
            session.add(AppSetting(key="workflow_step_retention_days", value=json.dumps(days)))
        else:
            row.value = json.dumps(days)
        await session.commit()


async def _seed() -> dict[str, int]:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        await session.execute(delete(WorkflowRunStep))
        await session.execute(delete(WorkflowRun))
        wf = Workflow(name="retention-wf")
        session.add(wf)
        await session.flush()
        ids: dict[str, int] = {}
        for key, finished in (
            ("old", now - timedelta(days=60)),
            ("recent", now - timedelta(days=1)),
            ("live", None),
        ):
            run = WorkflowRun(workflow_id=wf.id, run_number=len(ids) + 1, finished_at=finished)
            session.add(run)
            await session.flush()
            step = WorkflowRunStep(run_id=run.id, input_context="x" * 5000)
            session.add(step)
            await session.flush()
            ids[key] = step.id
        await session.commit()
        return ids


async def _inputs(ids: dict[str, int]) -> dict[str, str | None]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(WorkflowRunStep.id, WorkflowRunStep.input_context).where(
                WorkflowRunStep.id.in_(ids.values())
            )
        )
        by_id = {int(i): c for i, c in rows}
    return {k: by_id[v] for k, v in ids.items()}


def test_prunes_only_inputs_of_long_finished_runs() -> None:
    with TestClient(create_app()):
        pass
    ids = asyncio.run(_seed())

    asyncio.run(_set(0))
    assert asyncio.run(prune_expired_workflow_step_inputs()).rows == 0

    asyncio.run(_set(30))
    preview = asyncio.run(prune_expired_workflow_step_inputs(dry_run=True))
    assert preview.rows == 1 and preview.bytes > 4000
    assert asyncio.run(_inputs(ids))["old"] == "x" * 5000

    assert asyncio.run(prune_expired_workflow_step_inputs()).rows == 1
    got = asyncio.run(_inputs(ids))
    assert got["old"] == PRUNED_PLACEHOLDER
    assert got["recent"] == "x" * 5000
    assert got["live"] == "x" * 5000
    assert asyncio.run(prune_expired_workflow_step_inputs()).rows == 0
