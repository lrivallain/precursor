"""Files mode: edits made in the app are written to the definition files.

In files mode a file is the only declaration of a linked agent or workflow, so
a save from the app must land in it — a database write alone would be
overridden by the file on the next load. The file is regenerated from the
edited row in the same layout the export uses.

- An agent or workflow with no file yet gets one on its first save, so the
  folder fills up as things are edited.
- A file with errors is never overwritten from the app: that would silently
  throw away whatever the user was in the middle of writing by hand.
- Comments in a file are not kept when the app rewrites it (PyYAML can't
  round-trip them); the header comment is written back.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import yaml
from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.models import AgentSession, Role, Workflow, WorkflowRun, WorkflowStep
from precursor.backend.schemas.definitions import (
    AGENT_FILE_SUFFIX,
    WORKFLOW_FILE_SUFFIX,
    AgentDefinition,
    WorkflowDefinition,
)
from precursor.backend.schemas.definitions_api import DefinitionsExportResult
from precursor.backend.services.definitions import overlay, trust
from precursor.backend.services.definitions.exporter import (
    _ID_RE,
    _agent_document,
    _Context,
    _Plan,
    _short_slug,
    _workflow_document,
    _write_atomic,
    render_document,
)
from precursor.backend.services.definitions.loader import DefinitionSet, LoadedFile


async def _context(session: AsyncSession) -> _Context:
    roles = {r.id: r.name for r in (await session.execute(select(Role))).scalars()}
    root = overlay.definitions_root()
    return _Context(root=root, roles=roles, result=DefinitionsExportResult(root=str(root)))


def _refuse_broken(linked: LoadedFile | None) -> None:
    if linked is not None and linked.definition is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Its definition file {linked.path} has errors. Fix the file first — saving "
            "from here would overwrite it.",
        )


def _validate(
    model: type[AgentDefinition] | type[WorkflowDefinition], doc: dict[str, Any]
) -> AgentDefinition | WorkflowDefinition:
    try:
        return model.model_validate(doc)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'file'}: "
            + err["msg"].removeprefix("Value error, ")
            for err in exc.errors()
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"This can't be saved as a definition file: {problems}"
        ) from exc


def _identity(
    row: AgentSession | Workflow, kind: str, dset: DefinitionSet
) -> tuple[str, LoadedFile | None]:
    linked = dset.find("agent" if kind == "agent" else "workflow", row.export_id)
    if linked is not None:
        assert row.export_id is not None
        return row.export_id, linked
    ident = row.export_id
    # Reuse a portable id only if nothing else in the folder carries it.
    if not ident or not _ID_RE.match(ident) or dset.by_id.get(ident):
        ident = str(uuid.uuid4())
        row.export_id = ident
    return ident, None


def _new_path(dset: DefinitionSet, folder: str, label: str, fallback: str, suffix: str) -> str:
    plan = _Plan(taken_paths={p.lower() for p in dset.by_path})
    return plan.claim_path(
        overlay.definitions_root(), folder, _short_slug(label, 48) or fallback, suffix
    )


async def save_agent(session: AsyncSession, agent: AgentSession) -> str | None:
    """Write a listed agent's current declaration to its file; returns the path."""
    if not overlay.files_mode() or agent.inline:
        return None
    dset = overlay.current_definitions()
    ident, linked = _identity(agent, "agent", dset)
    _refuse_broken(linked)
    if linked is not None:
        path = linked.path
    else:
        adhoc = agent.topic_id is not None or agent.chat_id is not None
        path = _new_path(
            dset,
            "agents/adhoc" if adhoc else "agents",
            agent.title or "",
            "agent",
            AGENT_FILE_SUFFIX,
        )
    doc = _agent_document(await _context(session), agent, ident, path)
    defn = _validate(AgentDefinition, doc)
    await _refuse_carried_widening(session, agent, defn)
    # Saved from the app, so these are the permissions the user chose.
    trust.record(agent, defn)
    _write_atomic(overlay.definitions_root() / path, render_document(doc))
    overlay.invalidate()
    return path


async def _refuse_carried_widening(
    session: AsyncSession, row: AgentSession | Workflow, defn: AgentDefinition | WorkflowDefinition
) -> None:
    """Refuse a save that would accept a widening still waiting for review.

    A save writes the whole file and records it as accepted. Rebuilt from the
    row, it carries whatever the file grants now, reviewed or not — so if the
    version about to be written still includes a widening the file introduced
    on its own, saving would accept it unseen. A save that undoes it (the very
    edit someone makes to reject a change) goes through.
    """
    pending = await trust.pending_changes(session, row)
    if not pending:
        return
    accepted = await trust.accepted_for(session, row)
    carried = [
        c for c in trust.changes_between(row, accepted, trust.permissions_of(defn)) if c in pending
    ]
    if carried:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Its file has permission changes waiting for review "
            f"({'; '.join(carried)}). Review and accept them, or undo them, first.",
        )


async def _repin_if_running(
    session: AsyncSession, workflow: Workflow, path: str, text: str, defn: WorkflowDefinition
) -> None:
    """A save during a run applies to that run: move its pin (and the snapshot
    it would be restored from) to the version just written."""
    if not overlay.pinned(workflow) or workflow.current_run_id is None:
        return
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    overlay.pin_run(workflow.export_id or "", workflow.current_run_id, defn, path, digest)
    run = await session.get(WorkflowRun, workflow.current_run_id)
    if run is not None:
        run.definition_path, run.definition_hash, run.definition_snapshot = path, digest, text


async def _save_workflow_settings(
    session: AsyncSession, workflow: Workflow, ident: str, linked: LoadedFile
) -> str:
    path = overlay.definitions_root() / linked.path
    try:
        data = path.read_bytes()
        raw = yaml.safe_load(data.decode("utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Couldn't read {linked.path}: {exc}"
        ) from exc
    # The steps are kept as read here, so they must be the ones just checked.
    if hashlib.sha256(data).hexdigest() != linked.content_hash or not isinstance(raw, dict):
        overlay.invalidate()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{linked.path} changed on disk just now; save again"
        )
    doc = _workflow_document(await _context(session), workflow, ident, linked.path, {}, steps=[])
    doc["steps"] = raw.get("steps", [])
    defn = _validate(WorkflowDefinition, doc)
    assert isinstance(defn, WorkflowDefinition)
    await _refuse_carried_widening(session, workflow, defn)
    trust.record(workflow, defn)
    text = render_document(doc)
    _write_atomic(path, text)
    overlay.invalidate()
    await _repin_if_running(session, workflow, linked.path, text, defn)
    return linked.path


def _key_of(ref: str | None, ident: str) -> str | None:
    parts = overlay.split_ref(ref)
    return parts[1] if parts and parts[0] == ident else None


async def save_workflow(
    session: AsyncSession, workflow: Workflow, *, steps: bool = True
) -> str | None:
    """Write a workflow to its file; returns the path.

    ``steps=False`` (a settings-only edit) rewrites the settings and keeps the
    file's step list exactly as it is on disk: the rows may lag behind it
    (mid-run, or not yet read), and must not overwrite steps edited there.

    Otherwise step keys are kept wherever the step can still be recognised (its
    row or its private agent carries the key, or the file had the same agent at
    that position), so a save doesn't cut a step off from its history.
    """
    if not overlay.files_mode():
        return None
    dset = overlay.current_definitions()
    ident, linked = _identity(workflow, "workflow", dset)
    _refuse_broken(linked)
    if not steps and linked is not None:
        return await _save_workflow_settings(session, workflow, ident, linked)
    if workflow.status in overlay.ACTIVE_WORKFLOW_STATUSES:
        # The run is pinned to the step list it started with; new rows would be
        # projected from the wrong version.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Stop or finish the run before editing its steps"
        )
    path = (
        linked.path
        if linked is not None
        else _new_path(dset, "workflows", workflow.name or "", "workflow", WORKFLOW_FILE_SUFFIX)
    )

    await session.flush()
    rows = list(
        (
            await session.execute(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_id == workflow.id)
                .options(selectinload(WorkflowStep.agent))
                .order_by(WorkflowStep.position)
            )
        )
        .scalars()
        .all()
    )

    agent_paths: dict[int, str] = {}
    for step in rows:
        agent = step.agent
        if agent is None or agent.inline or agent.id in agent_paths:
            continue
        own = dset.find("agent", agent.export_id)
        agent_paths[agent.id] = (
            own.path if own is not None else (await save_agent(session, agent) or "")
        )
        dset = overlay.current_definitions()

    old = linked.definition if linked is not None else None
    old_steps = old.steps if isinstance(old, WorkflowDefinition) else []
    preferred: list[str | None] = []
    for pos, step in enumerate(rows):
        key = _key_of(step.definition_ref, ident)
        if key is None and step.agent is not None and step.agent.inline:
            key = _key_of(step.agent.definition_ref, ident)
        if key is None and pos < len(old_steps):
            before = old_steps[pos]
            same_agent = step.agent is not None and before.agent == agent_paths.get(step.agent.id)
            both_approval = step.kind == "approval" and before.kind == "approval"
            if same_agent or both_approval:
                key = before.key
        preferred.append(key)

    ctx = await _context(session)
    doc = _workflow_document(
        ctx, workflow, ident, path, agent_paths, steps=rows, preferred_keys=preferred
    )
    defn = _validate(WorkflowDefinition, doc)
    await _refuse_carried_widening(session, workflow, defn)
    trust.record(workflow, defn)
    _write_atomic(overlay.definitions_root() / path, render_document(doc))
    overlay.invalidate()

    for step, step_doc in zip(rows, doc["steps"], strict=True):
        ref = overlay.make_ref(ident, step_doc["key"])
        step.definition_ref = ref
        if step.agent is not None and step.agent.inline:
            step.agent.definition_ref = ref
    return path


def remove_file(row: AgentSession | Workflow) -> None:
    """Delete the file of an agent or workflow deleted in the app.

    Left behind, the next list would bring it straight back as a new row.
    """
    if not overlay.files_mode():
        return
    linked = overlay.linked_file(row)
    if linked is None or (isinstance(row, AgentSession) and row.inline):
        return
    (overlay.definitions_root() / linked.path).unlink(missing_ok=True)
    overlay.invalidate()
