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

from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from precursor.backend.models import AgentSession, Workflow, WorkflowRun, WorkflowStep
from precursor.backend.schemas.definitions import (
    AgentDefinition,
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from precursor.backend.services.definitions import overlay, trust
from precursor.backend.services.definitions.loader import DefinitionSet

ACTIVE_STATUSES = frozenset({"running", "paused", "awaiting_approval"})


@dataclass
class SyncResult:
    changed: bool = False
    # Why the workflow can't be run from its file; ``None`` when it can (or
    # when it has no file and keeps running from the database).
    error: str | None = None
    # Steps whose agent file can't be used (missing, moved, broken). The
    # engine skips a step with no agent, which for a gate means a silent pass,
    # so these block the run instead.
    unresolved: list[str] = field(default_factory=list)


async def _link(session: AsyncSession, row: AgentSession | WorkflowStep, ref: str) -> None:
    """Tie ``row`` to its file step without touching ``updated_at``.

    Linking is bookkeeping, not an edit. The in-memory object is updated too,
    as if it had been loaded that way.
    """
    model = type(row)
    await session.execute(
        update(model)
        .where(model.id == row.id)
        .values(definition_ref=ref, updated_at=model.updated_at)
        .execution_options(synchronize_session=False)
    )
    set_committed_value(row, "definition_ref", ref)


class DefinitionFileError(ValueError):
    """A workflow or agent can't run because its definition file is unusable,
    or grants permissions nobody has accepted yet."""


async def guard_continuation(session: AsyncSession, workflow: Workflow) -> bool:
    """Before a run that stopped (paused, awaiting approval, failed) goes on.

    It goes on with the file version it started from: the pin it holds, or the
    snapshot stored with the run (after a restart, or a failure released the
    pin). Checks on the file as it is now don't apply then — that version is for
    the next run — except that the step rows must still be the run's own (a
    list after a failure may have reshaped them to the current file). Only a run
    from before snapshots existed falls back to the current file, which must
    then be valid, have no permission change waiting for review, and have the
    same steps. Raises :class:`DefinitionFileError`.

    Returns ``True`` when it (re)pinned the run: the caller's loaded rows were
    projected before that, and must be reloaded.
    """
    if not overlay.files_mode():
        return False
    ident = workflow.export_id or ""
    if overlay.pinned(workflow):
        held_defn = overlay._pinned_definition(ident)
        if held_defn is not None and not same_steps(workflow, held_defn):
            overlay.release_pin(ident, workflow.current_run_id)
            raise DefinitionFileError(
                "this workflow's steps were reshaped to its file since the run started; "
                "start a new run"
            )
        return False
    run = (
        await session.get(WorkflowRun, workflow.current_run_id)
        if workflow.current_run_id is not None
        else None
    )
    if run is None:
        return False
    if overlay.pin_from_snapshot(ident, run.id, run.definition_path, run.definition_snapshot):
        pinned_defn = overlay._pinned_definition(ident)
        if pinned_defn is None or not same_steps(workflow, pinned_defn):
            overlay.release_pin(ident, run.id)
            raise DefinitionFileError(
                "this workflow's steps were reshaped to its file since the run started; "
                "start a new run"
            )
        return True
    if (problem := overlay.definition_error(workflow)) is not None:
        raise DefinitionFileError(problem)
    snap = overlay.read_workflow_file(workflow)
    if snap is None:
        return False  # not declared by a file
    path, digest, _text, defn = snap
    if (held := await review_blockers(session, workflow, defn)) is not None:
        raise DefinitionFileError(held)
    if not same_steps(workflow, defn):
        raise DefinitionFileError(
            f"the steps in {path} changed since this run started; cancel it and start a new run"
        )
    overlay.pin_run(ident, run.id, defn, path, digest)
    return True


def same_steps(workflow: Workflow, defn: WorkflowDefinition) -> bool:
    """Whether ``workflow``'s step rows are exactly ``defn``'s steps, in order."""
    ident = workflow.export_id or ""
    wanted = [overlay.make_ref(ident, step.key) for step in defn.steps]
    rows = [s.definition_ref for s in sorted(workflow.steps, key=lambda s: s.position)]
    return wanted == rows


async def restore_pins(session: AsyncSession) -> int:
    """At startup: re-pin every run in flight to the version it started from."""
    if not overlay.files_mode():
        return 0
    rows = await session.execute(
        select(
            Workflow.export_id,
            WorkflowRun.id,
            WorkflowRun.definition_path,
            WorkflowRun.definition_snapshot,
        )
        .join(WorkflowRun, WorkflowRun.id == Workflow.current_run_id)
        .where(
            Workflow.status.in_(ACTIVE_STATUSES),
            Workflow.export_id.is_not(None),
            WorkflowRun.definition_snapshot.is_not(None),
        )
    )
    restored = 0
    for export_id, run_id, path, text in rows.all():
        restored += overlay.pin_from_snapshot(export_id, run_id, path, text)
    return restored


async def review_blockers(
    session: AsyncSession, workflow: Workflow, definition: WorkflowDefinition | None = None
) -> str | None:
    """Why ``workflow`` can't start until someone reviews its files; ``None`` if it can.

    Covers the workflow's own file and those of the listed agents its steps run:
    each would otherwise be refused one step at a time, mid-run.
    """
    if not overlay.files_mode():
        return None
    held: list[str] = []
    if changes := await trust.pending_changes(session, workflow, definition):
        held.append(trust.review_message(workflow.name, changes))
    agent_ids = {s.agent_id for s in workflow.steps if s.agent_id is not None}
    for agent_id in sorted(agent_ids):
        agent = await session.get(AgentSession, agent_id)
        if agent is None or agent.inline:
            continue
        if changes := await trust.pending_changes(session, agent):
            held.append(trust.review_message(agent.title, changes))
    return " ".join(held) or None


async def _listed_agent_for(
    session: AsyncSession, dset: DefinitionSet, path: str, result: SyncResult
) -> int | None:
    """The agent row behind ``path``, creating it for a file new to this instance."""
    target = dset.by_path.get(path)
    if target is None or target.raw_id is None or dset.find("agent", target.raw_id) is None:
        result.unresolved.append(path)
        return None
    agent = (
        await session.execute(
            select(AgentSession)
            .where(AgentSession.export_id == target.raw_id, AgentSession.inline.is_(False))
            .limit(1)
        )
    ).scalar_one_or_none()
    if agent is not None:
        if not isinstance(target.definition, AgentDefinition):
            result.unresolved.append(path)
        return agent.id
    if not isinstance(target.definition, AgentDefinition):
        result.unresolved.append(path)
        return None
    # A file nobody exported from this database: the row is only its execution
    # anchor, declared by the file from the first load on.
    agent = AgentSession(
        export_id=target.raw_id,
        status="waiting",
        accepted_permissions=trust.NOTHING_ACCEPTED,
        **overlay.agent_columns(target.definition),
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
            await _link(session, current, ref)
            result.changed = True
        overlay.project_agent(current)
        return current.id
    # A step that left the file and came back (a cut and paste, a pull then a
    # revert) finds the private agent it had, with its history.
    kept = (
        await session.execute(
            select(AgentSession)
            .where(AgentSession.inline.is_(True), AgentSession.definition_ref == ref)
            .order_by(AgentSession.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if kept is not None:
        overlay.project_agent(kept)
        result.changed = True
        return kept.id
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
            await _link(session, row, ref)
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

    # A removed step's private agent is kept, keyed by its step: the step may
    # come back (a cut and paste, a pull then a revert) and take its history
    # with it. Deleting the workflow in the app removes them for good.

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
    # An id shared by two files resolves to no file, which would otherwise
    # read as "not linked" and run the database copy.
    if (problem := overlay.definition_error(workflow)) is not None:
        return SyncResult(error=problem)
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
    try:
        result = await _reconcile(session, workflow, linked.definition, dset)
        if result.changed:
            await session.commit()
    except IntegrityError:
        # A concurrent request reconciled (or adopted) the same file first;
        # its rows stand, and the next read sees them.
        await session.rollback()
        return SyncResult()
    if result.unresolved:
        result.error = (
            f"{linked.path} runs agent files that can't be used "
            f"({', '.join(dict.fromkeys(result.unresolved))}): missing, moved or broken"
        )
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


async def adopt_new_files(session: AsyncSession) -> bool:
    """Give every valid file new to this instance a row, so it shows up and runs.

    A file written by hand, or brought from another machine, has an ``id`` no
    row carries yet. The row created here is only its anchor: from the first
    load on, the file declares it. ``True`` if any row was created.
    """
    if not overlay.files_mode():
        return False
    dset = overlay.current_definitions()
    agent_files = [(f, d) for f, d in dset.agents() if dset.find("agent", f.raw_id) is f]
    workflow_files = [(f, d) for f, d in dset.workflows() if dset.find("workflow", f.raw_id) is f]
    created = False
    if agent_files:
        known = set(
            (
                await session.execute(
                    select(AgentSession.export_id).where(
                        AgentSession.export_id.in_([f.raw_id for f, _ in agent_files])
                    )
                )
            ).scalars()
        )
        for f, defn in agent_files:
            if f.raw_id not in known:
                session.add(
                    AgentSession(
                        export_id=f.raw_id,
                        status="waiting",
                        # Nobody here accepted what this file grants yet.
                        accepted_permissions=trust.NOTHING_ACCEPTED,
                        **overlay.agent_columns(defn),
                    )
                )
                created = True
    if workflow_files:
        known = set(
            (
                await session.execute(
                    select(Workflow.export_id).where(
                        Workflow.export_id.in_([f.raw_id for f, _ in workflow_files])
                    )
                )
            ).scalars()
        )
        for f, wf_defn in workflow_files:
            if f.raw_id not in known:
                session.add(
                    Workflow(
                        export_id=f.raw_id,
                        status="draft",
                        accepted_permissions=trust.NOTHING_ACCEPTED,
                        **overlay.workflow_columns(wf_defn),
                    )
                )
                created = True
    if created:
        try:
            await session.commit()
        except IntegrityError:
            # Two lists raced to adopt the same file: the other one won.
            await session.rollback()
            return False
    return created


async def refresh(session: AsyncSession) -> None:
    """Pick up a batch of file changes now (after a ``git pull``, say) rather
    than on the next list: new files get rows, step rows follow their files."""
    if not overlay.files_mode():
        return
    overlay.invalidate()
    await adopt_new_files(session)
    await sync_all(session)
