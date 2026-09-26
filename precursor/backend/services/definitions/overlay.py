"""Files mode: agents and workflows take their declarations from definition files.

With ``PRECURSOR_DEFINITIONS_SOURCE=files`` every row linked to a definition
file is *projected* from it whenever SQLAlchemy loads or refreshes it: the
declaration attributes are replaced by the file's values through
``set_committed_value``, which marks nothing dirty, so they are never written
back. The database columns are left as they were (stale until they are
dropped), and every reader — the runtime, the API, search, the MCP server —
sees the file without having to change.

What links a row to its file:

- a listed agent or a workflow: its ``export_id`` is the file's ``id``;
- a step's private agent: ``definition_ref`` = ``<workflow file id>/<step key>``;
- a workflow step row: the same ``definition_ref``.

A row with no file keeps its database values — transitional, until the export
has written one. A file with errors is not projected, and the runtime refuses to
start a run from it (:func:`definition_error`) rather than silently falling back
to the old database copy.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from precursor.backend.config import get_settings
from precursor.backend.models import AgentSession, Role, Workflow, WorkflowStep
from precursor.backend.schemas.definitions import (
    AgentDefinition,
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from precursor.backend.schemas.definitions_api import DefinitionSource
from precursor.backend.services.definitions.loader import (
    DefinitionSet,
    LoadedFile,
    load_definitions,
)

# How long a scan of the folder is reused. Loads happen per row (a list of 200
# agents is 200 loads), so the folder is walked at most this often; an edit on
# disk shows up within that delay.
SNAPSHOT_MAX_AGE = 1.0

_lock = threading.Lock()
_snapshot: tuple[float, Path, DefinitionSet] | None = None
_role_ids: dict[str, int] = {}
# Workflow file id → the definition its current run started from. While a run
# is active its steps (and their private agents) are projected from this, not
# from the file as it is now: a mid-run edit neither changes a run in flight
# nor lets an unreviewed permission change slip into it.
_pins: dict[str, WorkflowDefinition] = {}

ACTIVE_WORKFLOW_STATUSES = frozenset({"running", "paused", "awaiting_approval"})


def files_mode() -> bool:
    return get_settings().definitions_source == "files"


def definitions_root() -> Path:
    return Path(get_settings().definitions_dir)


def current_definitions() -> DefinitionSet:
    global _snapshot
    root = definitions_root()
    now = time.monotonic()
    with _lock:
        if _snapshot is not None and _snapshot[1] == root and now - _snapshot[0] < SNAPSHOT_MAX_AGE:
            return _snapshot[2]
    dset = load_definitions(root)
    with _lock:
        _snapshot = (now, root, dset)
    return dset


def invalidate() -> None:
    """Forget the cached scan, e.g. right after Precursor wrote a file."""
    global _snapshot
    with _lock:
        _snapshot = None


# --- Roles ------------------------------------------------------------------
# Files name a role; rows hold its id. Listeners are synchronous and can't
# query, so the name → id map is kept here and refreshed at startup and on every
# role change.


async def refresh_role_cache(session: AsyncSession) -> None:
    rows = (await session.execute(select(Role.id, Role.name))).all()
    fresh = {name.lower(): role_id for role_id, name in rows}
    with _lock:
        _role_ids.clear()
        _role_ids.update(fresh)


def _role_id(name: str | None) -> int | None:
    if not name:
        return None
    with _lock:
        return _role_ids.get(name.lower())


# Kept current from wherever a role changes (the roles API, a transfer import,
# the demo seed) rather than by each caller remembering to refresh.


@event.listens_for(Role, "after_insert")
def _role_inserted(_mapper: object, _connection: object, target: Role) -> None:
    with _lock:
        _role_ids[target.name.lower()] = target.id


@event.listens_for(Role, "after_update")
def _role_updated(_mapper: object, _connection: object, target: Role) -> None:
    old_names = inspect(target).attrs.name.history.deleted
    with _lock:
        for old in old_names:
            if isinstance(old, str) and _role_ids.get(old.lower()) == target.id:
                del _role_ids[old.lower()]
        _role_ids[target.name.lower()] = target.id


@event.listens_for(Role, "after_delete")
def _role_deleted(_mapper: object, _connection: object, target: Role) -> None:
    with _lock:
        if _role_ids.get(target.name.lower()) == target.id:
            del _role_ids[target.name.lower()]


# --- File → column values ---------------------------------------------------


def _scope(servers: list[str] | None) -> str | None:
    return None if servers is None else ",".join(servers)


def agent_columns(defn: AgentDefinition) -> dict[str, Any]:
    caps = defn.capabilities
    return {
        "title": defn.title,
        "task_prompt": defn.prompt,
        "model": defn.model,
        "role_id": _role_id(defn.role),
        "approval_policy": defn.approval_policy,
        "autonomy_enabled": defn.autonomy.enabled,
        "max_steps": defn.autonomy.max_steps,
        "use_mcp": caps.mcp,
        "use_skills": caps.skills,
        "use_memory": caps.memory,
        "mcp_servers": _scope(caps.mcp_servers),
        "token_budget": defn.limits.token_budget,
        "max_retries": defn.limits.max_retries,
    }


def vessel_columns(step: WorkflowStepDefinition) -> dict[str, Any]:
    """A step's private agent: its prompt and model, and defaults for the rest.

    Everything else a prompt step can say (capabilities, context, policies) is
    on the step itself, so the hidden agent must not contribute stale values of
    its own.
    """
    prompt = step.prompt or ""
    return {
        "title": (step.name or prompt).strip()[:200] or "Inline step",
        "task_prompt": prompt,
        "model": step.model,
        "role_id": None,
        "approval_policy": None,
        "autonomy_enabled": False,
        "max_steps": 12,
        "use_mcp": True,
        "use_skills": True,
        "use_memory": True,
        "mcp_servers": None,
        "token_budget": None,
        "max_retries": 0,
    }


def workflow_columns(defn: WorkflowDefinition) -> dict[str, Any]:
    return {
        "name": defn.name,
        "description": defn.description,
        "icon": defn.icon,
        "color": defn.color,
        "role_id": _role_id(defn.role),
        "approval_policy": defn.approval_policy,
        "clear_artifacts": defn.clear_artifacts,
        "max_loops": defn.max_loops,
        "step_timeout_seconds": defn.step_timeout_seconds,
    }


def step_columns(defn: WorkflowDefinition, index: int) -> dict[str, Any]:
    """A step's declaration as row values. Position and agent are structure,
    kept on the row by :mod:`anchors`, so they are not part of this."""
    position = {s.key: i for i, s in enumerate(defn.steps)}
    step = defn.steps[index]
    sources = step.context.sources or []
    caps = step.capabilities
    return {
        "name": step.name,
        "kind": step.kind,
        "instructions": step.instructions,
        "on_fail_position": position[step.on_fail] if step.on_fail else None,
        "on_error": step.on_error,
        "max_retries": step.max_retries,
        "on_reject": step.on_reject,
        "context_mode": step.context.mode,
        "context_sources": ",".join(str(position[k]) for k in sources) or None,
        "use_mcp": caps.mcp,
        "use_skills": caps.skills,
        "use_memory": caps.memory,
        "mcp_servers": _scope(caps.mcp_servers),
    }


# --- Resolution -------------------------------------------------------------


def split_ref(ref: str | None) -> tuple[str, str] | None:
    if not ref or "/" not in ref:
        return None
    workflow_id, key = ref.split("/", 1)
    return (workflow_id, key) if workflow_id and key else None


def make_ref(workflow_file_id: str, step_key: str) -> str:
    return f"{workflow_file_id}/{step_key}"


def pin_workflow(workflow: Workflow) -> None:
    """Freeze ``workflow``'s current file for the run that is starting."""
    linked = linked_file(workflow)
    if (
        workflow.export_id
        and linked is not None
        and isinstance(linked.definition, WorkflowDefinition)
    ):
        with _lock:
            _pins[workflow.export_id] = linked.definition


def pinned(workflow: Workflow) -> bool:
    with _lock:
        return bool(workflow.export_id) and workflow.export_id in _pins


def _pinned_definition(workflow_file_id: str) -> WorkflowDefinition | None:
    with _lock:
        return _pins.get(workflow_file_id)


def _unpin(workflow_file_id: str | None) -> None:
    if workflow_file_id:
        with _lock:
            _pins.pop(workflow_file_id, None)


def _step_def(dset: DefinitionSet, ref: str | None) -> tuple[WorkflowDefinition, int] | None:
    """The workflow definition and index a step ref points into — the pinned
    one while its workflow is mid-run, else the file as it is."""
    parts = split_ref(ref)
    if parts is None:
        return None
    defn = _pinned_definition(parts[0])
    if defn is None:
        wf_file = dset.find("workflow", parts[0])
        if wf_file is None or not isinstance(wf_file.definition, WorkflowDefinition):
            return None
        defn = wf_file.definition
    for index, step in enumerate(defn.steps):
        if step.key == parts[1]:
            return defn, index
    return None


def linked_file(obj: AgentSession | Workflow | WorkflowStep) -> LoadedFile | None:
    """The file ``obj`` is declared by, valid or not; ``None`` when it has none."""
    dset = current_definitions()
    if isinstance(obj, Workflow):
        return dset.find("workflow", obj.export_id)
    if isinstance(obj, WorkflowStep) or obj.inline:
        parts = split_ref(obj.definition_ref)
        return dset.find("workflow", parts[0]) if parts else None
    return dset.find("agent", obj.export_id)


def _ambiguous(obj: AgentSession | Workflow) -> list[str]:
    """The files sharing ``obj``'s id, when more than one does."""
    if isinstance(obj, AgentSession) and obj.inline:
        parts = split_ref(obj.definition_ref)
        ident = parts[0] if parts else None
    else:
        ident = obj.export_id
    files = current_definitions().by_id.get(ident or "", ())
    return [f.path for f in files] if len(files) > 1 else []


def definition_error(obj: AgentSession | Workflow) -> str | None:
    """Why ``obj`` can't run from its file, in files mode; ``None`` when it can.

    Only a row that *has* a file can fail this way: one with no file yet keeps
    running from the database. An id carried by several files (a copy made
    without changing it) counts as a file with errors — which one declares the
    row is anyone's guess, and the database copy is no answer either.
    """
    if not files_mode():
        return None
    if shared := _ambiguous(obj):
        return f"its id is used by several files ({', '.join(shared)}); give each its own id"
    linked = linked_file(obj)
    if linked is None:
        return None
    if linked.definition is None:
        return f"its definition file {linked.path} has errors; fix it, then run again"
    ref = obj.definition_ref if isinstance(obj, AgentSession) and obj.inline else None
    if ref is not None and _step_def(current_definitions(), ref) is None:
        return f"its step is no longer in {linked.path}"
    return None


def source_of(obj: AgentSession | Workflow) -> DefinitionSource | None:
    """What the API reports as ``definition``; ``None`` outside files mode."""
    if not files_mode():
        return None
    shared = _ambiguous(obj)
    if shared:
        return DefinitionSource(state="invalid", path=shared[0], message=definition_error(obj))
    linked = linked_file(obj)
    if linked is None:
        return DefinitionSource(
            state="none", message="No definition file yet; still declared by the database"
        )
    problem = definition_error(obj)
    if problem is not None:
        return DefinitionSource(state="invalid", path=linked.path, message=problem)
    return DefinitionSource(state="file", path=linked.path, content_hash=linked.content_hash)


def provenance(obj: AgentSession | Workflow) -> tuple[str, str] | None:
    """``(path, sha256)`` of the file ``obj`` runs from, in files mode."""
    if not files_mode():
        return None
    linked = linked_file(obj)
    if linked is None or linked.definition is None:
        return None
    return linked.path, linked.content_hash


# --- Projection -------------------------------------------------------------


def _apply(target: object, values: dict[str, Any], only: Collection[str] | None = None) -> None:
    for key, value in values.items():
        if only is None or key in only:
            set_committed_value(target, key, value)


def project_agent(agent: AgentSession, only: Collection[str] | None = None) -> None:
    if not files_mode():
        return
    dset = current_definitions()
    if agent.inline:
        found = _step_def(dset, agent.definition_ref)
        if found is not None:
            defn, index = found
            step = defn.steps[index]
            if step.prompt is not None:
                _apply(agent, vessel_columns(step), only)
        return
    linked = dset.find("agent", agent.export_id)
    if linked is not None and isinstance(linked.definition, AgentDefinition):
        _apply(agent, agent_columns(linked.definition), only)


def project_workflow(workflow: Workflow, only: Collection[str] | None = None) -> None:
    if not files_mode():
        return
    # ``status`` is a plain column, loaded before this runs. A run that ended
    # releases its pin; the next one pins the file as it is then.
    if workflow.status in ACTIVE_WORKFLOW_STATUSES:
        frozen = _pinned_definition(workflow.export_id or "")
        if frozen is not None:
            _apply(workflow, workflow_columns(frozen), only)
            return
    else:
        _unpin(workflow.export_id)
    linked = current_definitions().find("workflow", workflow.export_id)
    if linked is not None and isinstance(linked.definition, WorkflowDefinition):
        _apply(workflow, workflow_columns(linked.definition), only)


def project_step(step: WorkflowStep, only: Collection[str] | None = None) -> None:
    if not files_mode():
        return
    found = _step_def(current_definitions(), step.definition_ref)
    if found is not None:
        defn, index = found
        _apply(step, step_columns(defn, index), only)


# A refresh reloads only ``attrs`` (all of them when it's ``None``). Re-project
# just those: after a flush SQLAlchemy may reload one expired column, and
# re-applying the whole file then would wipe edits still held in memory.


@event.listens_for(AgentSession, "load")
def _agent_loaded(target: AgentSession, _context: object) -> None:
    project_agent(target)


@event.listens_for(AgentSession, "refresh")
def _agent_refreshed(target: AgentSession, _context: object, attrs: Collection[str] | None) -> None:
    project_agent(target, attrs)


@event.listens_for(Workflow, "load")
def _workflow_loaded(target: Workflow, _context: object) -> None:
    project_workflow(target)


@event.listens_for(Workflow, "refresh")
def _workflow_refreshed(target: Workflow, _context: object, attrs: Collection[str] | None) -> None:
    project_workflow(target, attrs)


@event.listens_for(WorkflowStep, "load")
def _step_loaded(target: WorkflowStep, _context: object) -> None:
    project_step(target)


@event.listens_for(WorkflowStep, "refresh")
def _step_refreshed(target: WorkflowStep, _context: object, attrs: Collection[str] | None) -> None:
    project_step(target, attrs)
