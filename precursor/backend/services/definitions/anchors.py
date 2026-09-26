"""Files mode: keep a workflow's step rows in line with its definition file.

The workflow engine drives ``WorkflowStep`` rows: their position, the agent
each one runs and the per-run counters. In files mode those rows are only
*anchors* — one per step of the file, tied to it by ``definition_ref`` — and
everything a step declares is projected onto them from the file on load (see
:mod:`overlay`). This module adds, removes, reorders and re-points the anchors
when the file's step list changes.

It never touches a workflow in the middle of a run: the engine keeps the step
list it started with, and the file's new shape applies from the next run.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AgentSession, Workflow, WorkflowStep
from precursor.backend.schemas.definitions import (
    AgentDefinition,
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from precursor.backend.services.definitions import overlay
from precursor.backend.services.definitions.loader import DefinitionSet

ACTIVE_STATUSES = frozenset({"running", "paused", "awaiting_approval"})


@dataclass
class SyncResult:
    changed: bool = False
    # Why the workflow can't be run from its file; ``None`` when it can (or
    # when it has no file and keeps running from the database).
    error: str | None = None


class DefinitionFileError(ValueError):
    """A workflow or agent can't run because its definition file is unusable."""


async def _listed_agent_for(
    session: AsyncSession, dset: DefinitionSet, path: str, result: SyncResult
) -> int | None:
    """The agent row behind ``path``, creating it for a file new to this instance."""
    target = dset.by_path.get(path)
    if target is None or target.raw_id is None or dset.find("agent", target.raw_id) is None:
        return None
    agent = (
        await session.execute(
            select(AgentSession)
            .where(AgentSession.export_id == target.raw_id, AgentSession.inline.is_(False))
            .limit(1)
        )
    ).scalar_one_or_none()
    if agent is not None:
        return agent.id
    if not isinstance(target.definition, AgentDefinition):
        return None
    # A file nobody exported from this database: the row is only its execution
    # anchor, declared by the file from the first load on.
    agent = AgentSession(
        export_id=target.raw_id, status="waiting", **overlay.agent_columns(target.definition)
    )
    session.add(agent)
    await session.flush()
    result.changed = True
    return agent.id


async def _vessel_for(
    session: AsyncSession,
    step: WorkflowStepDefinition,
    ref: str,
    row: WorkflowStep | None,
    result: SyncResult,
) -> int:
    """The private agent behind a prompt step, reusing the one it already had."""
    current = (
        await session.get(AgentSession, row.agent_id)
        if row is not None and row.agent_id is not None
        else None
    )
    if current is not None and current.inline and current.definition_ref in (None, ref):
        if current.definition_ref != ref:
            current.definition_ref = ref
            result.changed = True
        overlay.project_agent(current)
        return current.id
    vessel = AgentSession(
        inline=True, definition_ref=ref, status="waiting", **overlay.vessel_columns(step)
    )
    session.add(vessel)
    await session.flush()
    result.changed = True
    return vessel.id


async def _desired_agent(
    session: AsyncSession,
    dset: DefinitionSet,
    step: WorkflowStepDefinition,
    ref: str,
    row: WorkflowStep | None,
    result: SyncResult,
) -> int | None:
    if step.kind == "approval":
        return None
    if step.prompt is not None:
        return await _vessel_for(session, step, ref, row, result)
    assert step.agent is not None
    return await _listed_agent_for(session, dset, step.agent, result)


async def _reconcile(
    session: AsyncSession, workflow: Workflow, defn: WorkflowDefinition, dset: DefinitionSet
) -> SyncResult:
    result = SyncResult()
    rows = list(
        (
            await session.execute(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_id == workflow.id)
                .order_by(WorkflowStep.position)
            )
        )
        .scalars()
        .all()
    )
    refs = [overlay.make_ref(defn.id, step.key) for step in defn.steps]

    # First time this workflow is read from its file: its rows predate files
    # mode. The export wrote the file in row order, so adopt them by position —
    # which keeps each step's private agent, and with it the run history.
    if rows and all(r.definition_ref is None for r in rows):
        for row, ref in zip(rows, refs, strict=False):
            row.definition_ref = ref
        result.changed = True

    by_ref: dict[str, WorkflowStep] = {}
    removed: list[WorkflowStep] = []
    for row in rows:
        if row.definition_ref and row.definition_ref not in by_ref:
            by_ref[row.definition_ref] = row
        else:
            removed.append(row)

    desired: list[tuple[WorkflowStep | None, str, int | None, int]] = []
    for index, (step, ref) in enumerate(zip(defn.steps, refs, strict=True)):
        match: WorkflowStep | None = by_ref.pop(ref) if ref in by_ref else None
        agent_id = await _desired_agent(session, dset, step, ref, match, result)
        desired.append((match, ref, agent_id, index))
    removed.extend(by_ref.values())

    orphan_vessels = {r.agent_id for r in removed if r.agent_id is not None}
    for row in removed:
        await session.delete(row)
        result.changed = True
    if removed:
        await session.flush()

    kept = [(row, index) for row, _, _, index in desired if row is not None]
    if any(row.position != index for row, index in kept):
        # Two passes around the (workflow, position) unique constraint.
        for n, (row, _) in enumerate(kept):
            row.position = -(n + 1)
        await session.flush()
        for row, index in kept:
            row.position = index
        result.changed = True

    for anchor, ref, agent_id, index in desired:
        if anchor is None:
            session.add(
                WorkflowStep(
                    workflow_id=workflow.id,
                    position=index,
                    agent_id=agent_id,
                    definition_ref=ref,
                    **overlay.step_columns(defn, index),
                )
            )
            result.changed = True
        else:
            if anchor.agent_id != agent_id:
                anchor.agent_id = agent_id
                result.changed = True
            overlay.project_step(anchor)
    await session.flush()

    in_use = {agent_id for _, _, agent_id, _ in desired if agent_id is not None}
    for vessel_id in orphan_vessels - in_use:
        vessel = await session.get(AgentSession, vessel_id)
        if vessel is None or not vessel.inline:
            continue
        # SQLite runs with foreign keys off, so detach anything still pointing
        # at it ourselves, as the step editor does.
        await session.execute(
            update(WorkflowStep).where(WorkflowStep.agent_id == vessel_id).values(agent_id=None)
        )
        await session.delete(vessel)
        result.changed = True

    status = "idle" if defn.steps else "draft"
    if workflow.status in ("idle", "draft") and workflow.status != status:
        workflow.status = status
        result.changed = True
    return result


async def sync_workflow(session: AsyncSession, workflow_id: int) -> SyncResult:
    """Bring one workflow's anchors in line with its file (files mode only)."""
    if not overlay.files_mode():
        return SyncResult()
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        return SyncResult()
    dset = overlay.current_definitions()
    linked = dset.find("workflow", workflow.export_id)
    if linked is None:
        return SyncResult()
    if not isinstance(linked.definition, WorkflowDefinition):
        return SyncResult(
            error=f"its definition file {linked.path} has errors; fix it, then run again"
        )
    if workflow.status in ACTIVE_STATUSES:
        return SyncResult()
    result = await _reconcile(session, workflow, linked.definition, dset)
    if result.changed:
        await session.commit()
    return result


async def sync_all(session: AsyncSession) -> bool:
    """Sync every linked workflow that isn't mid-run; ``True`` if anything changed."""
    if not overlay.files_mode():
        return False
    dset = overlay.current_definitions()
    linked_ids = [f.raw_id for f in dset.files if f.kind == "workflow" and f.raw_id]
    if not linked_ids:
        return False
    ids = (
        (await session.execute(select(Workflow.id).where(Workflow.export_id.in_(linked_ids))))
        .scalars()
        .all()
    )
    changed = False
    for workflow_id in ids:
        changed = (await sync_workflow(session, workflow_id)).changed or changed
    return changed
