"""Migrating an install from database-declared to file-declared agents and workflows.

The step before the database's declaration columns can go: every agent and
workflow must be written to a file that says exactly what the database says,
and the switch must change nothing about how they run. Three operations:

- :func:`preview` — what the migration would do, and what stands in its way.
  Read-only.
- :func:`migrate` — snapshot the database, write the missing files, rewrite the
  ones that differ from the database, **verify** every file against the
  database, accept the permissions they grant, and switch to files mode. If the
  verification finds any difference, the switch isn't made.
- :func:`revert` — copy every file's declaration back into the database
  columns and switch back to database mode. Possible until the columns are
  dropped.

Until the switch the database is the source, so where a file and the database
disagree the database wins. A file that already matches is left as it is,
comments and layout included.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.config import get_settings
from precursor.backend.models import AgentSession, AppSetting, Role, Workflow, WorkflowStep
from precursor.backend.schemas.definitions import AgentDefinition, WorkflowDefinition
from precursor.backend.schemas.definitions_api import (
    CleanupPreview,
    DefinitionIssue,
    DefinitionKind,
    DefinitionsExportResult,
    DefinitionsStatus,
    DefinitionsWorkspaceRef,
    FinalizedRecord,
    FinalizeResult,
    MigrationItem,
    MigrationItemDetail,
    MigrationPreview,
    MigrationRecord,
    MigrationResult,
    RevertResult,
)
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.definitions import anchors, overlay, trust
from precursor.backend.services.definitions.exporter import (
    _agent_document,
    _Context,
    _workflow_document,
    _write_atomic,
    export_definitions,
    render_document,
)
from precursor.backend.services.definitions.home import (
    definitions_workspace,
    ensure_definitions_workspace,
)
from precursor.backend.services.definitions.loader import (
    DefinitionSet,
    LoadedFile,
    load_definitions,
)
from precursor.backend.services.definitions.writer import preferred_step_keys

ACTIVE = overlay.ACTIVE_WORKFLOW_STATUSES
# What the switch to files did, for the wizard: ``{"at", "snapshot", …}``.
MIGRATED_SETTING_KEY = "definitions_migrated"


# --- Planning ---------------------------------------------------------------


@dataclass
class _Planned:
    item: MigrationItem
    # The document the database says, when the row has a file to compare it
    # with (``regenerate`` / ``unchanged``).
    doc: dict[str, Any] | None = None


@dataclass
class _Plan:
    root: Path
    dset: DefinitionSet
    planned: list[_Planned] = field(default_factory=list)
    issues: list[DefinitionIssue] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def items(self) -> list[MigrationItem]:
        return [p.item for p in self.planned]

    def count(self, action: str) -> int:
        return sum(1 for p in self.planned if p.item.action == action)


def _dump(defn: AgentDefinition | WorkflowDefinition) -> dict[str, Any]:
    return defn.model_dump(mode="json", by_alias=True)


def _differences(generated: dict[str, Any], current: dict[str, Any]) -> list[str]:
    keys = sorted(set(generated) | set(current))
    return [k for k in keys if generated.get(k) != current.get(k)]


def _validated(
    kind: DefinitionKind, doc: dict[str, Any], path: str, issues: list[DefinitionIssue]
) -> AgentDefinition | WorkflowDefinition | None:
    model: type[AgentDefinition] | type[WorkflowDefinition] = (
        AgentDefinition if kind == "agent" else WorkflowDefinition
    )
    try:
        return model.model_validate(doc)
    except ValidationError as exc:
        for err in exc.errors():
            issues.append(
                DefinitionIssue(
                    severity="error",
                    path=path,
                    location=".".join(str(p) for p in err["loc"]) or None,
                    message=err["msg"].removeprefix("Value error, ")
                    + " — it will be written as-is and refuse to run until fixed",
                )
            )
        return None


def _compare(
    kind: DefinitionKind,
    row_id: int,
    name: str,
    linked: LoadedFile,
    doc: dict[str, Any],
    issues: list[DefinitionIssue],
) -> _Planned:
    generated = _validated(kind, doc, linked.path, issues)
    if not isinstance(linked.definition, AgentDefinition | WorkflowDefinition):
        action, reason = "regenerate", "its file has errors"
    elif generated is None:
        action, reason = "regenerate", "the database's version doesn't validate"
    else:
        differs = _differences(_dump(generated), _dump(linked.definition))
        if differs:
            action, reason = "regenerate", f"differs from the database: {', '.join(differs)}"
        else:
            action, reason = "unchanged", None
    return _Planned(
        MigrationItem(
            kind=kind, id=row_id, name=name, path=linked.path, action=action, reason=reason
        ),
        doc=doc,
    )


async def _plan(session: AsyncSession) -> _Plan:
    root = overlay.definitions_root()
    dset = await asyncio.to_thread(load_definitions, root)
    plan = _Plan(root=root, dset=dset)

    for ident, files in sorted(dset.by_id.items()):
        if len(files) > 1 and any(f.kind != "summary" for f in files):
            plan.blockers.append(
                f"id '{ident}' is used by several files ({', '.join(f.path for f in files)}); "
                "give each its own id"
            )
    active = (
        (await session.execute(select(Workflow.name).where(Workflow.status.in_(ACTIVE))))
        .scalars()
        .all()
    )
    if active:
        plan.blockers.append(
            f"{', '.join(repr(n) for n in active)} "
            f"{'is' if len(active) == 1 else 'are'} mid-run; let it finish or cancel it first"
        )
    if not await resolve_agents_enabled(session):
        plan.blockers.append("Agents mode is off")

    roles = {r.id: r.name for r in (await session.execute(select(Role))).scalars()}
    ctx = _Context(root=root, roles=roles, result=DefinitionsExportResult(root=str(root)))

    agents = (
        (
            await session.execute(
                select(AgentSession).where(AgentSession.inline.is_(False)).order_by(AgentSession.id)
            )
        )
        .scalars()
        .all()
    )
    agent_paths: dict[int, str] = {}
    known_agent_ids: set[str] = set()
    for agent in agents:
        if agent.export_id:
            known_agent_ids.add(agent.export_id)
        linked = dset.find("agent", agent.export_id)
        if linked is None:
            plan.planned.append(
                _Planned(
                    MigrationItem(kind="agent", id=agent.id, name=agent.title, action="create")
                )
            )
            # Its path is chosen when it's written; a stand-in keeps the
            # workflows that run it comparable meanwhile.
            agent_paths[agent.id] = f"agents/new-agent-{agent.id}.agent.yaml"
            _agent_document(ctx, agent, agent.export_id or "new", "(new file)")  # for its warnings
            continue
        agent_paths[agent.id] = linked.path
        assert agent.export_id is not None
        doc = _agent_document(ctx, agent, agent.export_id, linked.path)
        plan.planned.append(_compare("agent", agent.id, agent.title, linked, doc, plan.issues))

    workflows = (
        (
            await session.execute(
                select(Workflow)
                .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
                .order_by(Workflow.id)
            )
        )
        .scalars()
        .all()
    )
    known_workflow_ids: set[str] = set()
    for workflow in workflows:
        if workflow.export_id:
            known_workflow_ids.add(workflow.export_id)
        linked = dset.find("workflow", workflow.export_id)
        if linked is None:
            plan.planned.append(
                _Planned(
                    MigrationItem(
                        kind="workflow", id=workflow.id, name=workflow.name, action="create"
                    )
                )
            )
            rows = sorted(workflow.steps, key=lambda s: s.position)
            doc = _workflow_document(
                ctx, workflow, workflow.export_id or "new", "(new file)", agent_paths, steps=rows
            )
            _validated("workflow", doc, f"workflow '{workflow.name}' (new file)", plan.issues)
            continue
        assert workflow.export_id is not None
        rows = sorted(workflow.steps, key=lambda s: s.position)
        old = linked.definition if isinstance(linked.definition, WorkflowDefinition) else None
        keys = preferred_step_keys(rows, workflow.export_id, old, agent_paths)
        doc = _workflow_document(
            ctx,
            workflow,
            workflow.export_id,
            linked.path,
            agent_paths,
            steps=rows,
            preferred_keys=keys,
        )
        plan.planned.append(
            _compare("workflow", workflow.id, workflow.name, linked, doc, plan.issues)
        )

    for f in dset.files:
        if f.kind not in ("agent", "workflow"):
            # Summary templates have no database side: nothing to migrate.
            continue
        if f.raw_id is None:
            # Not even an id to read: nothing can tell which agent or workflow it
            # declares, so it's left as it is (and keeps showing in the check).
            plan.issues.append(
                DefinitionIssue(
                    severity="warning",
                    path=f.path,
                    message="can't be read, so it isn't matched to anything; it's left as it "
                    "is — fix or remove it",
                )
            )
            continue
        known = known_agent_ids if f.kind == "agent" else known_workflow_ids
        if f.definition is not None and f.raw_id not in known:
            plan.planned.append(
                _Planned(
                    MigrationItem(
                        kind=f.kind,
                        name=f.name or f.path,
                        path=f.path,
                        action="new_from_disk",
                        reason="added after the switch; its permissions wait for review",
                    )
                )
            )
    plan.issues.extend(ctx.result.issues)
    return plan


async def _workspace_ref(session: AsyncSession) -> DefinitionsWorkspaceRef | None:
    ws = await definitions_workspace(session)
    return DefinitionsWorkspaceRef(id=ws.id, slug=ws.slug, name=ws.name) if ws else None


def _source() -> Literal["database", "files"]:
    return "files" if overlay.files_mode() else "database"


def _sample_path(dset: DefinitionSet) -> str | None:
    """A file to open from the Settings panel: a workflow if there is one."""
    ordered = sorted(
        (f for f in dset.files if f.kind in ("agent", "workflow")),
        key=lambda f: (f.kind != "workflow", f.path),
    )
    return ordered[0].path if ordered else None


async def _read_record(session: AsyncSession, key: str) -> dict[str, Any] | None:
    row = await session.get(AppSetting, key)
    if row is None or not row.value:
        return None
    value = json.loads(row.value)
    return value if isinstance(value, dict) else None


async def _write_record(session: AsyncSession, key: str, value: dict[str, Any] | None) -> None:
    row = await session.get(AppSetting, key)
    if value is None:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(AppSetting(key=key, value=json.dumps(value)))
    else:
        row.value = json.dumps(value)


async def preview(session: AsyncSession) -> MigrationPreview:
    migrated = await _read_record(session, MIGRATED_SETTING_KEY)
    migrated_record = MigrationRecord(**migrated) if migrated else None
    if (done := overlay.finalized()) is not None:
        return MigrationPreview(
            stage="finalized",
            migrated=migrated_record,
            finalized=FinalizedRecord(**done),
            source="files",
            forced=overlay.source_forced(),
            folder=str(overlay.definitions_root()),
            workspace=await _workspace_ref(session),
            sample_path=_sample_path(overlay.current_definitions()),
            blockers=["The migration is finished: the database no longer holds declarations"],
        )
    if overlay.files_mode():
        return MigrationPreview(
            stage="files",
            migrated=migrated_record,
            cleanup=await cleanup_preview(session),
            source="files",
            forced=overlay.source_forced(),
            folder=str(overlay.definitions_root()),
            workspace=await _workspace_ref(session),
            sample_path=_sample_path(overlay.current_definitions()),
            blockers=["Agents and workflows are already declared by their files"],
        )
    plan = await _plan(session)
    has_errors = any(i.severity == "error" for i in plan.issues)
    return MigrationPreview(
        stage="database",
        source="database",
        folder=str(plan.root),
        workspace=await _workspace_ref(session),
        sample_path=_sample_path(plan.dset),
        items=plan.items(),
        issues=plan.issues,
        blockers=plan.blockers,
        ready=not plan.blockers,
        needs_confirmation=bool(plan.count("regenerate")) or has_errors,
    )


async def item_detail(
    session: AsyncSession, kind: DefinitionKind, row_id: int
) -> MigrationItemDetail:
    """The file the migration would write for one agent or workflow, and the
    one on disk now — the content behind a preview line."""
    if overlay.files_mode():
        raise MigrationRefused("Agents and workflows are already declared by their files")
    plan = await _plan(session)
    planned = next((p for p in plan.planned if p.item.kind == kind and p.item.id == row_id), None)
    if planned is None or planned.item.action == "new_from_disk":
        raise LookupError(f"No {kind} {row_id} in the migration")
    doc = planned.doc
    if doc is None:
        doc = await _new_document(session, kind, row_id)
    current: str | None = None
    if planned.item.path:
        try:
            current = (plan.root / planned.item.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
    action = planned.item.action
    assert action in ("create", "regenerate", "unchanged")
    return MigrationItemDetail(
        kind=kind,
        id=row_id,
        name=planned.item.name,
        action=action,
        path=planned.item.path,
        proposed=render_document(doc),
        current=current,
    )


async def _new_document(session: AsyncSession, kind: DefinitionKind, row_id: int) -> dict[str, Any]:
    """What the export would write for a row with no file yet (id shown as-is,
    or as a placeholder when it will be minted at migration)."""
    root = overlay.definitions_root()
    roles = {r.id: r.name for r in (await session.execute(select(Role))).scalars()}
    ctx = _Context(root=root, roles=roles, result=DefinitionsExportResult(root=str(root)))
    if kind == "agent":
        agent = await session.get(AgentSession, row_id)
        assert agent is not None
        return _agent_document(ctx, agent, agent.export_id or "(assigned when migrating)", "")
    workflow = (
        await session.execute(
            select(Workflow)
            .where(Workflow.id == row_id)
            .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
        )
    ).scalar_one()
    dset = overlay.current_definitions()
    agent_paths: dict[int, str] = {}
    for step in workflow.steps:
        if step.agent is not None and not step.agent.inline:
            linked = dset.find("agent", step.agent.export_id)
            agent_paths[step.agent.id] = (
                linked.path if linked is not None else "agents/(new file).agent.yaml"
            )
    rows = sorted(workflow.steps, key=lambda s: s.position)
    return _workflow_document(
        ctx,
        workflow,
        workflow.export_id or "(assigned when migrating)",
        "",
        agent_paths,
        steps=rows,
    )


# --- Snapshot ---------------------------------------------------------------


def _snapshot_sync(action: str) -> str | None:
    from precursor.backend.services.backup import _sqlite_file_path, _vacuum_into

    settings = get_settings()
    src = _sqlite_file_path(settings.database_url)
    if src is None or not src.exists():
        return None
    folder = Path(settings.data_dir).resolve() / "definitions-migration"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = folder / f"precursor-{stamp}-before-{action}.db"
    _vacuum_into(src, dest)
    return str(dest)


async def _snapshot(action: str) -> str | None:
    """A copy of the database (SQLite) to go back to; ``None`` otherwise."""
    return await asyncio.to_thread(_snapshot_sync, action)


# --- Migrate ----------------------------------------------------------------


class MigrationRefused(Exception):
    def __init__(self, message: str, preview: MigrationPreview | None = None) -> None:
        super().__init__(message)
        self.preview = preview


async def _accept_all(session: AsyncSession, dset: DefinitionSet, rows: Sequence[Any]) -> None:
    """Record every file's permissions as accepted: they are what the database had."""
    for row in rows:
        kind: DefinitionKind = "workflow" if isinstance(row, Workflow) else "agent"
        linked = dset.find(kind, row.export_id)
        if linked is None or not isinstance(
            linked.definition, AgentDefinition | WorkflowDefinition
        ):
            continue
        snapshot = trust.permissions_of(linked.definition)
        model = type(row)
        await session.execute(
            update(model)
            .where(model.id == row.id)
            .values(
                accepted_permissions=json.dumps(snapshot, sort_keys=True),
                updated_at=model.updated_at,
            )
            .execution_options(synchronize_session=False)
        )


async def migrate(session: AsyncSession, *, acknowledge: bool = False) -> MigrationResult:
    """Move the install to files mode, or refuse (:class:`MigrationRefused`)."""
    before = await preview(session)
    if before.blockers:
        raise MigrationRefused("; ".join(before.blockers), before)
    if before.needs_confirmation and not acknowledge:
        raise MigrationRefused(
            "Some files will be rewritten from the database, or some definitions have "
            "errors; review the preview and confirm",
            before,
        )

    snapshot = await _snapshot("migration")
    await ensure_definitions_workspace(session)
    root = overlay.definitions_root()

    # 1. A file for every agent and workflow that has none (linked, accepted).
    exported = await export_definitions(session, root)
    # 2. The files that differ from the database, rewritten from it.
    plan = await _plan(session)
    for planned in plan.planned:
        if planned.item.action == "regenerate" and planned.doc is not None and planned.item.path:
            _write_atomic(root / planned.item.path, render_document(planned.doc))
    overlay.invalidate()

    # 3. Verify: every agent and workflow now has one valid file saying exactly
    #    what the database says. Anything else and the switch isn't made.
    check = await _plan(session)
    mismatches = [
        f"{p.item.kind} '{p.item.name}': {p.item.reason or p.item.action}"
        for p in check.planned
        if p.item.action in ("create", "regenerate")
    ]
    mismatches += check.blockers
    created = len(exported.written)
    result = MigrationResult(
        ok=not mismatches,
        source="database",
        snapshot=snapshot,
        created=created,
        regenerated=plan.count("regenerate"),
        # Just-created files match by construction; the rest were already right.
        unchanged=max(0, plan.count("unchanged") - created),
        added_from_disk=plan.count("new_from_disk"),
        issues=exported.issues + [i for i in plan.issues if i not in exported.issues],
        mismatches=mismatches,
    )
    if mismatches:
        await session.commit()
        return result

    # 4. Accept what the files grant — it is what the database had — and switch.
    agents = (
        (await session.execute(select(AgentSession).where(AgentSession.inline.is_(False))))
        .scalars()
        .all()
    )
    workflows = (await session.execute(select(Workflow))).scalars().all()
    await _accept_all(session, check.dset, [*agents, *workflows])
    await _write_record(
        session,
        MIGRATED_SETTING_KEY,
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "snapshot": snapshot,
            "created": result.created,
            "regenerated": result.regenerated,
            "unchanged": result.unchanged,
            "added_from_disk": result.added_from_disk,
        },
    )
    await session.commit()
    await overlay.persist_source(session, "files")
    # Step rows are tied to their file steps, and files nobody here had yet
    # become agents and workflows (held for review).
    await anchors.refresh(session)
    result.source = "files"
    return result


# --- Revert -----------------------------------------------------------------


async def revert(session: AsyncSession) -> RevertResult:
    """Copy the files' declarations into the database, then use the database."""
    if (done := overlay.finalized()) is not None:
        raise MigrationRefused(
            f"The database was cleaned up on {done.get('at', '?')}; switching back isn't possible "
            "any more. Restoring the database copy taken then is the only way back"
            + (f" ({done['snapshot']})" if done.get("snapshot") else "")
        )
    if not overlay.files_mode():
        raise MigrationRefused("Agents and workflows are already declared by the database")
    if overlay.source_forced():
        raise MigrationRefused(
            "Files mode is forced by PRECURSOR_DEFINITIONS_SOURCE=files; unset it first"
        )
    active = (
        (await session.execute(select(Workflow.name).where(Workflow.status.in_(ACTIVE))))
        .scalars()
        .all()
    )
    if active:
        raise MigrationRefused(
            f"{', '.join(repr(n) for n in active)} "
            f"{'is' if len(active) == 1 else 'are'} mid-run; let it finish or cancel it first"
        )

    snapshot = await _snapshot("revert")
    overlay.invalidate()
    result = RevertResult(ok=True, source="files", snapshot=snapshot)

    agents = (
        (await session.execute(select(AgentSession).where(AgentSession.inline.is_(False))))
        .scalars()
        .all()
    )
    for agent in agents:
        linked = overlay.linked_file(agent)
        if linked is None:
            continue  # never had a file: the database already declares it
        if not isinstance(linked.definition, AgentDefinition):
            result.skipped.append(f"agent '{agent.title}': {linked.path} has errors")
            continue
        await session.execute(
            update(AgentSession)
            .where(AgentSession.id == agent.id)
            .values(**overlay.agent_columns(linked.definition), updated_at=AgentSession.updated_at)
            .execution_options(synchronize_session=False)
        )
        result.agents += 1

    workflow_ids = (await session.execute(select(Workflow.id))).scalars().all()
    for workflow_id in workflow_ids:
        synced = await anchors.sync_workflow(session, workflow_id)
        workflow = await session.get(Workflow, workflow_id)
        if workflow is None:
            continue
        linked = overlay.linked_file(workflow)
        if linked is None:
            continue
        if synced.error is not None or not isinstance(linked.definition, WorkflowDefinition):
            result.skipped.append(
                f"workflow '{workflow.name}': {synced.error or 'file has errors'}"
            )
            continue
        defn = linked.definition
        await session.execute(
            update(Workflow)
            .where(Workflow.id == workflow_id)
            .values(**overlay.workflow_columns(defn), updated_at=Workflow.updated_at)
            .execution_options(synchronize_session=False)
        )
        index = {overlay.make_ref(defn.id, s.key): i for i, s in enumerate(defn.steps)}
        rows = (
            (
                await session.execute(
                    select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            at = index.get(row.definition_ref or "")
            if at is None:
                continue
            await session.execute(
                update(WorkflowStep)
                .where(WorkflowStep.id == row.id)
                .values(**overlay.step_columns(defn, at), updated_at=WorkflowStep.updated_at)
                .execution_options(synchronize_session=False)
            )
            step = defn.steps[at]
            if step.prompt is not None and row.agent_id is not None:
                await session.execute(
                    update(AgentSession)
                    .where(AgentSession.id == row.agent_id, AgentSession.inline.is_(True))
                    .values(**overlay.vessel_columns(step), updated_at=AgentSession.updated_at)
                    .execution_options(synchronize_session=False)
                )
        result.workflows += 1

    await _write_record(session, MIGRATED_SETTING_KEY, None)
    await session.commit()
    await overlay.persist_source(session, "database")
    with overlay._lock:
        overlay._pins.clear()
    result.source = "database"
    return result


# --- Cleanup (the last, irreversible step) ---------------------------------

# What a cleaned-up row holds in its declaration columns: the model defaults.
# Names are kept — search, ordering and name matching run on them in SQL, and
# they are re-synced from the files (``anchors.sync_names``).
_BLANK_AGENT: dict[str, Any] = {
    "task_prompt": "",
    "model": None,
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
_BLANK_WORKFLOW: dict[str, Any] = {
    "description": None,
    "icon": None,
    "color": None,
    "role_id": None,
    "approval_policy": None,
    "clear_artifacts": True,
    "max_loops": 3,
    "step_timeout_seconds": None,
}
_BLANK_STEP: dict[str, Any] = {
    "name": None,
    "kind": "task",
    "instructions": None,
    "on_fail_position": None,
    "on_error": "fail",
    "max_retries": 0,
    "on_reject": "rework",
    "context_mode": "auto",
    "context_sources": None,
    "use_mcp": None,
    "use_skills": None,
    "use_memory": None,
    "mcp_servers": None,
}


async def cleanup_preview(session: AsyncSession) -> CleanupPreview:
    """What cleaning up would clear, and what must be fixed before it can."""
    from sqlalchemy import func

    from precursor.backend.services.definitions.checker import check_definitions

    preview = CleanupPreview()
    if overlay.finalized() is not None or not overlay.files_mode():
        return preview
    dset = await asyncio.to_thread(load_definitions, overlay.definitions_root())

    active = (
        (await session.execute(select(Workflow.name).where(Workflow.status.in_(ACTIVE))))
        .scalars()
        .all()
    )
    if active:
        preview.blockers.append(
            f"{', '.join(repr(n) for n in active)} "
            f"{'is' if len(active) == 1 else 'are'} mid-run; let it finish or cancel it first"
        )
    # Once the database's copy is gone, the files are all there is: they must
    # all be usable first. A summary template doesn't declare anything the
    # database holds, so its errors don't stand in the way.
    for issue in check_definitions(dset):
        owner = dset.by_path.get(issue.path or "")
        if owner is not None and owner.kind == "summary":
            continue
        if issue.severity == "error":
            where = ": ".join(p for p in (issue.path, issue.location) if p)
            preview.blockers.append(f"{where}: {issue.message}" if where else issue.message)

    agents = (
        (await session.execute(select(AgentSession).where(AgentSession.inline.is_(False))))
        .scalars()
        .all()
    )
    workflows = (await session.execute(select(Workflow))).scalars().all()
    for agent in agents:
        if dset.find("agent", agent.export_id) is None and not dset.by_id.get(
            agent.export_id or ""
        ):
            preview.missing_files.append(f"agent '{agent.title}'")
        elif changes := await trust.pending_changes(session, agent):
            preview.warnings.append(
                f"agent '{agent.title}' has permission changes waiting for review "
                f"({'; '.join(changes)}); they stay pending"
            )
    for workflow in workflows:
        if dset.find("workflow", workflow.export_id) is None and not dset.by_id.get(
            workflow.export_id or ""
        ):
            preview.missing_files.append(f"workflow '{workflow.name}'")
        elif changes := await trust.pending_changes(session, workflow):
            preview.warnings.append(
                f"workflow '{workflow.name}' has permission changes waiting for review "
                f"({'; '.join(changes)}); they stay pending"
            )

    preview.agents = len(agents)
    preview.workflows = len(workflows)
    preview.step_prompts = (
        await session.execute(
            select(func.count()).select_from(AgentSession).where(AgentSession.inline.is_(True))
        )
    ).scalar_one()
    preview.steps = (
        await session.execute(select(func.count()).select_from(WorkflowStep))
    ).scalar_one()
    preview.ready = not preview.blockers
    return preview


async def _verify_all_declared(session: AsyncSession) -> list[str]:
    """Everything that would be left with no declaration once the columns are blank."""
    overlay.invalidate()
    dset = overlay.current_definitions()
    problems: list[str] = []
    for agent in (
        (await session.execute(select(AgentSession).where(AgentSession.inline.is_(False))))
        .scalars()
        .all()
    ):
        linked = dset.find("agent", agent.export_id)
        if linked is None or linked.definition is None:
            problems.append(f"agent '{agent.title}' has no valid file")
    for workflow in (
        (
            await session.execute(
                select(Workflow).options(
                    selectinload(Workflow.steps).selectinload(WorkflowStep.agent)
                )
            )
        )
        .scalars()
        .all()
    ):
        linked = dset.find("workflow", workflow.export_id)
        if linked is None or not isinstance(linked.definition, WorkflowDefinition):
            problems.append(f"workflow '{workflow.name}' has no valid file")
            continue
        if not anchors.same_steps(workflow, linked.definition):
            problems.append(f"workflow '{workflow.name}': its steps aren't tied to its file")
            continue
        for step in workflow.steps:
            vessel = step.agent
            if (
                vessel is not None
                and vessel.inline
                and overlay.definition_error(vessel) is not None
            ):
                problems.append(
                    f"workflow '{workflow.name}': a step's prompt can't be read from its file"
                )
    return problems


async def finalize(session: AsyncSession, *, confirm: bool) -> FinalizeResult:
    """Clean the declarations out of the database. The last step; no way back.

    Refused unless confirmed, in files mode, with nothing mid-run and every
    file valid. Files still missing are written first (from the database, which
    still declares those rows), everything is verified, a copy of the database
    is taken, and only then are the declaration columns reset.
    """
    if not confirm:
        raise MigrationRefused("Cleaning up the database can't be undone; confirm it first")
    if overlay.finalized() is not None:
        raise MigrationRefused("The database was already cleaned up")
    if not overlay.files_mode():
        raise MigrationRefused("Migrate to definition files first")
    ready = await cleanup_preview(session)
    if not ready.ready:
        raise MigrationRefused("; ".join(ready.blockers))

    snapshot = await _snapshot("cleanup")
    root = overlay.definitions_root()
    exported = await export_definitions(session, root)
    await anchors.refresh(session)
    session.expire_all()
    problems = await _verify_all_declared(session)
    if problems:
        return FinalizeResult(
            ok=False,
            written=len(exported.written),
            issues=[DefinitionIssue(severity="error", message=p) for p in problems],
        )

    counts = {
        "agents": ready.agents,
        "step_prompts": ready.step_prompts,
        "workflows": ready.workflows,
        "steps": ready.steps,
    }
    for model, blank in (
        (AgentSession, _BLANK_AGENT),
        (Workflow, _BLANK_WORKFLOW),
        (WorkflowStep, _BLANK_STEP),
    ):
        await session.execute(
            update(model)
            .values(**blank, updated_at=model.updated_at)
            .execution_options(synchronize_session=False)
        )
    await session.commit()
    record = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "snapshot": snapshot, **counts}
    await overlay.persist_source(session, "files")
    await overlay.persist_finalized(session, record)
    session.expire_all()
    return FinalizeResult(
        ok=True,
        finalized=FinalizedRecord(**record),
        written=len(exported.written),
        issues=exported.issues,
    )


async def status(session: AsyncSession) -> DefinitionsStatus:
    """How many agents and workflows are still declared by the database.

    Before the migration, every active one; after it, the ones with no file (a
    leftover the export writes). After the cleanup there is nothing left to
    migrate — a missing file is an error there, reported by the check.
    """
    from sqlalchemy import func

    if overlay.finalized() is not None:
        return DefinitionsStatus(stage="finalized", forced=overlay.source_forced())
    agents_q = select(AgentSession.export_id).where(
        AgentSession.inline.is_(False), AgentSession.archived_at.is_(None)
    )
    workflows_q = select(Workflow.export_id).where(Workflow.archived_at.is_(None))
    if not overlay.files_mode():
        agents = (
            await session.execute(select(func.count()).select_from(agents_q.subquery()))
        ).scalar_one()
        workflows = (
            await session.execute(select(func.count()).select_from(workflows_q.subquery()))
        ).scalar_one()
        return DefinitionsStatus(stage="database", agents=agents, workflows=workflows)
    dset = overlay.current_definitions()
    agent_ids = (await session.execute(agents_q)).scalars().all()
    workflow_ids = (await session.execute(workflows_q)).scalars().all()
    return DefinitionsStatus(
        stage="files",
        forced=overlay.source_forced(),
        agents=sum(1 for i in agent_ids if not dset.by_id.get(i or "")),
        workflows=sum(1 for i in workflow_ids if not dset.by_id.get(i or "")),
    )
