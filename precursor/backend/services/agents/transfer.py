"""Export and import of agents and workflows as portable YAML documents.

The unit of transfer is one object per file: an agent, or a workflow together
with the agents its steps need. What travels is the *definition* — prompts,
capability toggles, budgets, step wiring, the persona — and never runtime state
(status, run history, artifacts, token counters, the SDK session handle) nor
per-install secrets (webhook tokens). A file is therefore something you can read,
diff, commit, and hand to someone else.

Importing is two-phase on purpose. :func:`preview_document` reports name
collisions without writing anything, so the caller can offer the real choice —
**replace** the existing agent in place, **create** a second one, or **link** the
step to the one already there — and :func:`import_document` applies those
decisions. Matching prefers ``export_id`` (a stable portable identity minted on
first export) over the name, so re-importing a file that originally came from
this install updates the very object it came from instead of guessing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import yaml
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor import __version__
from precursor.backend.models import AgentSession, Role, Workflow, WorkflowStep
from precursor.backend.models.agent_schedule import AgentSchedule
from precursor.backend.models.workflow import (
    WORKFLOW_STEP_CONTEXT_MODES,
    WORKFLOW_STEP_ERROR_POLICIES,
    WORKFLOW_STEP_KINDS,
    WORKFLOW_STEP_REJECT_POLICIES,
)
from precursor.backend.schemas.transfer import (
    TRANSFER_FORMAT_VERSION,
    ConflictAction,
    TransferAgent,
    TransferConflict,
    TransferDocument,
    TransferImportResult,
    TransferPreview,
    TransferResolution,
    TransferRole,
    TransferSchedule,
    TransferStep,
    TransferWarning,
    TransferWorkflow,
)

logger = logging.getLogger(__name__)

# Guard against a pathological file wedging the parser before validation runs.
MAX_DOCUMENT_BYTES = 2_000_000


def _mint_export_id() -> str:
    return str(uuid.uuid4())


# --- Export -----------------------------------------------------------------


def _role_doc(role: Role | None) -> TransferRole | None:
    # The seeded ``default`` role injects nothing and exists everywhere, so
    # carrying it would just create noise (and a pointless conflict) on import.
    if role is None or role.is_default:
        return None
    return TransferRole(name=role.name, system_prompt=role.system_prompt or "")


def _agent_schedule_doc(schedule: AgentSchedule | None) -> TransferSchedule | None:
    if schedule is None:
        return None
    return TransferSchedule(
        interval_seconds=schedule.interval_seconds,
        run_at_minute=schedule.run_at_minute,
        timezone=schedule.timezone,
        days_of_week=schedule.days_of_week,
        clear_context=schedule.clear_context,
    )


def _workflow_schedule_doc(workflow: Workflow) -> TransferSchedule | None:
    # A workflow's cadence is inlined on its row; nothing is configured until one
    # of the two recurrence knobs is set.
    if workflow.interval_seconds is None and workflow.run_at_minute is None:
        return None
    return TransferSchedule(
        interval_seconds=workflow.interval_seconds,
        run_at_minute=workflow.run_at_minute,
        timezone=workflow.timezone,
        days_of_week=workflow.days_of_week,
    )


def _agent_doc(agent: AgentSession, role: Role | None) -> TransferAgent:
    return TransferAgent(
        export_id=agent.export_id,
        title=agent.title,
        task=agent.task_prompt or "",
        model=agent.model,
        autonomy_enabled=agent.autonomy_enabled,
        max_steps=agent.max_steps,
        approval_policy=agent.approval_policy,
        token_budget=agent.token_budget,
        max_retries=agent.max_retries,
        use_mcp=agent.use_mcp,
        use_skills=agent.use_skills,
        use_memory=agent.use_memory,
        role=_role_doc(role),
        schedule=_agent_schedule_doc(agent.schedule),
        inline=agent.inline,
    )


async def _role_for(session: AsyncSession, role_id: int | None) -> Role | None:
    return await session.get(Role, role_id) if role_id is not None else None


async def _ensure_export_id(session: AsyncSession, obj: AgentSession | Workflow) -> str:
    """Mint the object's portable identity the first time it is exported.

    Done lazily rather than at creation so existing rows pick one up naturally
    and nothing has to be backfilled.
    """
    if not obj.export_id:
        obj.export_id = _mint_export_id()
        await session.flush()
    return obj.export_id


def _header(kind: str) -> dict[str, Any]:
    return {
        "format": TRANSFER_FORMAT_VERSION,
        "kind": kind,
        "exported_by": f"Precursor {__version__}",
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


async def export_agent(session: AsyncSession, agent: AgentSession) -> TransferDocument:
    """Serialise a single agent (its persona and cadence travel with it)."""
    await _ensure_export_id(session, agent)
    doc = TransferDocument(
        **_header("agent"),
        agents=[_agent_doc(agent, await _role_for(session, agent.role_id))],
    )
    await session.commit()
    return doc


async def export_workflow(session: AsyncSession, workflow: Workflow) -> TransferDocument:
    """Serialise a workflow together with every agent its steps reference.

    An external (reusable) agent is embedded exactly like a step-private one —
    a pipeline that arrives without its agents isn't runnable. The difference
    only shows up at import: ``inline`` agents are recreated silently, while
    reusable ones are what the conflict resolution is offered for.
    """
    await _ensure_export_id(session, workflow)

    agents: list[TransferAgent] = []
    # Agent row id -> index in ``agents``, so a workflow that uses the same agent
    # in two steps embeds it once and both steps point at the same entry.
    index_of: dict[int, int] = {}
    steps: list[TransferStep] = []

    for step in sorted(workflow.steps, key=lambda s: s.position):
        agent_index: int | None = None
        agent = step.agent
        if agent is not None:
            if agent.id not in index_of:
                if not agent.inline:
                    # Only shareable agents get a portable identity; a private
                    # vessel is recreated fresh with its step every time.
                    await _ensure_export_id(session, agent)
                index_of[agent.id] = len(agents)
                agents.append(_agent_doc(agent, await _role_for(session, agent.role_id)))
            agent_index = index_of[agent.id]
        steps.append(
            TransferStep(
                agent=agent_index,
                name=step.name,
                kind=step.kind,
                on_fail_position=step.on_fail_position,
                instructions=step.instructions,
                on_error=step.on_error,
                max_retries=step.max_retries,
                on_reject=step.on_reject,
                context_mode=step.context_mode,
                context_sources=step.context_sources,
                use_mcp=step.use_mcp,
                use_skills=step.use_skills,
                use_memory=step.use_memory,
                mcp_servers=step.mcp_servers,
            )
        )

    doc = TransferDocument(
        **_header("workflow"),
        agents=agents,
        workflow=TransferWorkflow(
            export_id=workflow.export_id,
            name=workflow.name,
            description=workflow.description,
            icon=workflow.icon,
            color=workflow.color,
            clear_artifacts=workflow.clear_artifacts,
            max_loops=workflow.max_loops,
            step_timeout_seconds=workflow.step_timeout_seconds,
            role=_role_doc(await _role_for(session, workflow.role_id)),
            schedule=_workflow_schedule_doc(workflow),
            steps=steps,
        ),
    )
    await session.commit()
    return doc


def dump_yaml(doc: TransferDocument) -> str:
    """Render a document as YAML, dropping empty fields to keep files readable."""
    data = doc.model_dump(by_alias=True, exclude_none=True, exclude_defaults=False)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def suggested_filename(doc: TransferDocument) -> str:
    name = doc.workflow.name if doc.workflow else (doc.agents[0].title if doc.agents else "export")
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part) or doc.kind
    return f"{slug}.{doc.kind}.yaml"


# --- Parsing ----------------------------------------------------------------


def parse_document(content: str) -> TransferDocument:
    """Parse and validate a YAML file into a document, or raise a 400.

    Every failure mode here is user-facing (someone dropped the wrong file in),
    so the messages say what is wrong with *their* file rather than leaking a
    parser traceback.
    """
    if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is too large to import")
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Expected a single exported agent or workflow at the top level",
        )
    fmt = raw.get("format", TRANSFER_FORMAT_VERSION)
    if not isinstance(fmt, int) or fmt > TRANSFER_FORMAT_VERSION:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This file was written by a newer version (format {fmt}); upgrade Precursor to import it",
        )
    try:
        doc = TransferDocument.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid document: {exc}") from exc

    if doc.kind == "workflow":
        if doc.workflow is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workflow document has no workflow")
        for pos, step in enumerate(doc.workflow.steps):
            if step.agent is None:
                if step.kind != "approval":
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"Step {pos + 1} references no agent but is not an approval step",
                    )
            elif step.agent >= len(doc.agents):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Step {pos + 1} references agent #{step.agent}, which the file doesn't define",
                )
    elif len(doc.agents) != 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "An agent document must define exactly one agent"
        )
    return doc


# --- Conflict detection -----------------------------------------------------


async def _find_agent_match(
    session: AsyncSession, incoming: TransferAgent
) -> tuple[AgentSession | None, bool]:
    """Locate the existing agent an incoming one refers to.

    Returns ``(row, same_object)``. ``same_object`` is True only for an
    ``export_id`` hit — proof this *is* the agent the file came from, rather than
    a different agent that merely shares a name.
    """
    if incoming.export_id:
        found = (
            await session.execute(
                select(AgentSession).where(AgentSession.export_id == incoming.export_id)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found, True
    # Name match ignores archived rows and private vessels: neither is something
    # the user can pick in the Agents section, so colliding with one would offer
    # a choice about an object they can't even see.
    found = (
        await session.execute(
            select(AgentSession)
            .where(
                func.lower(AgentSession.title) == incoming.title.strip().lower(),
                AgentSession.inline.is_(False),
                AgentSession.archived_at.is_(None),
            )
            .order_by(AgentSession.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return found, False


async def _find_workflow_match(
    session: AsyncSession, incoming: TransferWorkflow
) -> tuple[Workflow | None, bool]:
    if incoming.export_id:
        found = (
            await session.execute(select(Workflow).where(Workflow.export_id == incoming.export_id))
        ).scalar_one_or_none()
        if found is not None:
            return found, True
    found = (
        await session.execute(
            select(Workflow)
            .where(
                func.lower(Workflow.name) == incoming.name.strip().lower(),
                Workflow.archived_at.is_(None),
            )
            .order_by(Workflow.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return found, False


async def _workflow_count_for(session: AsyncSession, agent_id: int) -> int:
    """How many live workflows reference this agent (the blast radius of a replace)."""
    return (
        await session.execute(
            select(func.count(func.distinct(Workflow.id)))
            .select_from(WorkflowStep)
            .join(Workflow, Workflow.id == WorkflowStep.workflow_id)
            .where(WorkflowStep.agent_id == agent_id, Workflow.archived_at.is_(None))
        )
    ).scalar_one() or 0


async def _model_warnings(session: AsyncSession, doc: TransferDocument) -> list[TransferWarning]:
    """Flag pinned models this install doesn't offer.

    Advisory only: the runtime may simply be down, and an agent whose model is
    unavailable still imports fine — it just falls back to the default at run
    time. Blocking the import over it would be worse than the warning.
    """
    pinned = {a.model for a in doc.agents if a.model}
    if not pinned:
        return []
    try:
        from precursor.backend.services.agents.manager import get_agent_manager

        available = {m.get("id") for m in await get_agent_manager().list_models()}
    except Exception:  # pragma: no cover - runtime unavailable
        return []
    if not available:
        return []
    missing = sorted(m for m in pinned if m not in available)
    return [
        TransferWarning(
            code="model",
            message=f"Model '{m}' isn't available here - the agent will use the default instead.",
        )
        for m in missing
    ]


async def preview_document(session: AsyncSession, doc: TransferDocument) -> TransferPreview:
    """Report what importing ``doc`` would collide with, without writing anything."""
    conflicts: list[TransferConflict] = []
    warnings: list[TransferWarning] = await _model_warnings(session, doc)

    for index, incoming in enumerate(doc.agents):
        # A private vessel belongs to its step, so it is always recreated — there
        # is no shared roster for it to collide in.
        if incoming.inline:
            continue
        existing, same_object = await _find_agent_match(session, incoming)
        if existing is None:
            continue
        conflicts.append(
            TransferConflict(
                kind="agent",
                index=index,
                name=incoming.title,
                existing_id=existing.id,
                existing_title=existing.title,
                same_object=same_object,
                workflow_count=await _workflow_count_for(session, existing.id),
                allowed=["replace", "create", "link"],
                # Only default to overwriting when the file provably describes
                # this very agent; a name collision alone is far too weak a
                # signal to destroy someone's prompt without them saying so.
                default="replace" if same_object else "link",
            )
        )

    if doc.workflow is not None:
        existing_wf, same_object = await _find_workflow_match(session, doc.workflow)
        if existing_wf is not None:
            conflicts.append(
                TransferConflict(
                    kind="workflow",
                    index=None,
                    name=doc.workflow.name,
                    existing_id=existing_wf.id,
                    existing_title=existing_wf.name,
                    same_object=same_object,
                    # A workflow isn't a shared resource the way an agent is, so
                    # "link" has no meaning here.
                    allowed=["replace", "create"],
                    default="replace" if same_object else "create",
                )
            )
        if doc.workflow.schedule is not None:
            warnings.append(
                TransferWarning(
                    code="schedule",
                    message="The recurrence was imported but left paused - enable it when you're ready.",
                )
            )
    elif doc.agents and doc.agents[0].schedule is not None:
        warnings.append(
            TransferWarning(
                code="schedule",
                message="The recurrence was imported but left paused - enable it when you're ready.",
            )
        )

    name = doc.workflow.name if doc.workflow else (doc.agents[0].title if doc.agents else "")
    return TransferPreview(
        kind=doc.kind,
        name=name,
        agent_count=len(doc.agents),
        step_count=len(doc.workflow.steps) if doc.workflow else 0,
        conflicts=conflicts,
        warnings=warnings,
    )


# --- Import -----------------------------------------------------------------


async def _resolve_role(session: AsyncSession, incoming: TransferRole | None) -> int | None:
    """Match a carried persona by name, creating it only if it's genuinely new.

    Roles are small, name-addressed and shared by convention, so importing two
    workflows that use the same persona should converge on one role rather than
    accumulating copies.
    """
    if incoming is None:
        return None
    name = incoming.name.strip()[:64]
    if not name:
        return None
    existing = (
        await session.execute(select(Role).where(func.lower(Role.name) == name.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    role = Role(name=name, system_prompt=incoming.system_prompt or "")
    session.add(role)
    await session.flush()
    return role.id


async def _unique_agent_title(session: AsyncSession, title: str) -> str:
    """Suffix a title until it no longer collides, so "create" stays legible."""
    base = title.strip()[:200] or "Imported agent"
    candidate = base
    n = 2
    while (
        await session.execute(
            select(AgentSession.id).where(
                func.lower(AgentSession.title) == candidate.lower(),
                AgentSession.inline.is_(False),
                AgentSession.archived_at.is_(None),
            )
        )
    ).first() is not None:
        suffix = f" ({n})"
        candidate = f"{base[: 200 - len(suffix)]}{suffix}"
        n += 1
    return candidate


async def _unique_workflow_name(session: AsyncSession, name: str) -> str:
    base = name.strip()[:200] or "Imported workflow"
    candidate = base
    n = 2
    while (
        await session.execute(
            select(Workflow.id).where(
                func.lower(Workflow.name) == candidate.lower(), Workflow.archived_at.is_(None)
            )
        )
    ).first() is not None:
        suffix = f" ({n})"
        candidate = f"{base[: 200 - len(suffix)]}{suffix}"
        n += 1
    return candidate


def _apply_agent_fields(
    agent: AgentSession, incoming: TransferAgent, *, title: str, role_id: int | None
) -> None:
    agent.title = title
    agent.task_prompt = incoming.task or ""
    agent.model = incoming.model
    agent.autonomy_enabled = incoming.autonomy_enabled
    agent.max_steps = incoming.max_steps
    agent.approval_policy = incoming.approval_policy
    agent.token_budget = incoming.token_budget
    agent.max_retries = incoming.max_retries
    agent.use_mcp = incoming.use_mcp
    agent.use_skills = incoming.use_skills
    agent.use_memory = incoming.use_memory
    agent.role_id = role_id


async def _apply_agent_schedule(
    session: AsyncSession, agent: AgentSession, incoming: TransferSchedule | None
) -> None:
    """Attach the carried cadence, always **disabled**.

    A file dropped into a new install describes *when* something should run, not
    a mandate to start running it; the owner arms it deliberately.
    """
    if incoming is None:
        return
    existing = (
        await session.execute(
            select(AgentSchedule).where(AgentSchedule.agent_session_id == agent.id)
        )
    ).scalar_one_or_none()
    schedule = existing or AgentSchedule(agent_session_id=agent.id)
    schedule.enabled = False
    schedule.clear_context = incoming.clear_context
    # Interval mode needs a value even in daily-at-time mode (the column is not
    # nullable); a day is the natural floor for a daily schedule.
    schedule.interval_seconds = incoming.interval_seconds or 86400
    schedule.run_at_minute = incoming.run_at_minute
    schedule.timezone = incoming.timezone
    schedule.days_of_week = incoming.days_of_week
    schedule.next_run_at = None
    if existing is None:
        session.add(schedule)
    await session.flush()


def _resolution_for(
    resolutions: list[TransferResolution],
    conflicts: list[TransferConflict],
    kind: str,
    index: int | None,
) -> ConflictAction | None:
    """The caller's decision for one conflict, else the preview's safe default."""
    for r in resolutions:
        if r.kind == kind and r.index == index:
            return r.action
    for c in conflicts:
        if c.kind == kind and c.index == index:
            return c.default
    return None


async def _materialise_agent(
    session: AsyncSession,
    incoming: TransferAgent,
    action: ConflictAction | None,
    existing: AgentSession | None,
    result: TransferImportResult,
) -> AgentSession:
    """Create, overwrite or reuse the row an incoming agent maps to."""
    role_id = await _resolve_role(session, incoming.role)

    if action == "link" and existing is not None:
        # Deliberately untouched: the point of linking is to keep the agent the
        # user already has, prompts and history intact.
        result.linked_agent_ids.append(existing.id)
        return existing

    if action == "replace" and existing is not None:
        # A replace means "this file is now the definition", so the incoming
        # title wins too — but the row keeps its id, which is what makes other
        # workflows pointing at it pick the new definition up.
        _apply_agent_fields(
            existing,
            incoming,
            title=incoming.title.strip()[:200] or existing.title,
            role_id=role_id,
        )
        if incoming.export_id:
            existing.export_id = incoming.export_id
        await _apply_agent_schedule(session, existing, incoming.schedule)
        result.replaced_agent_ids.append(existing.id)
        await session.flush()
        return existing

    title = (
        incoming.title.strip()[:200]
        if incoming.inline
        else await _unique_agent_title(session, incoming.title)
    )
    agent = AgentSession(status="waiting", inline=incoming.inline)
    # A fresh copy is a *different* agent from the one the file described, so it
    # must not inherit its portable identity — otherwise the next import of the
    # same file would silently target this copy.
    agent.export_id = None if action == "create" else (incoming.export_id or None)
    _apply_agent_fields(agent, incoming, title=title, role_id=role_id)
    session.add(agent)
    await session.flush()
    await _apply_agent_schedule(session, agent, incoming.schedule)
    if not incoming.inline:
        result.created_agent_ids.append(agent.id)
    return agent


async def import_document(
    session: AsyncSession, doc: TransferDocument, resolutions: list[TransferResolution]
) -> TransferImportResult:
    """Apply a parsed document, honouring one resolution per reported conflict."""
    preview = await preview_document(session, doc)
    result = TransferImportResult(
        kind=doc.kind,
        name=preview.name,
        warnings=preview.warnings,
    )

    # Resolve every embedded agent first: the workflow's steps are wired to the
    # rows this produces, whether they were created, overwritten or linked.
    agent_rows: list[AgentSession | None] = []
    for index, incoming in enumerate(doc.agents):
        if incoming.inline:
            agent_rows.append(await _materialise_agent(session, incoming, None, None, result))
            continue
        existing, _ = await _find_agent_match(session, incoming)
        action = _resolution_for(resolutions, preview.conflicts, "agent", index)
        if existing is None:
            action = None  # nothing to replace or link to
        agent_rows.append(await _materialise_agent(session, incoming, action, existing, result))

    if doc.kind == "agent":
        agent = agent_rows[0]
        assert agent is not None
        await session.commit()
        result.agent_id = agent.id
        result.name = agent.title
        return result

    assert doc.workflow is not None
    incoming_wf = doc.workflow
    wf_existing, _ = await _find_workflow_match(session, incoming_wf)
    wf_action = _resolution_for(resolutions, preview.conflicts, "workflow", None)

    role_id = await _resolve_role(session, incoming_wf.role)

    if wf_action == "replace" and wf_existing is not None:
        workflow = wf_existing
        if workflow.status == "running":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Cannot replace a workflow while it is running"
            )
        workflow.name = incoming_wf.name.strip()[:200] or workflow.name
        if incoming_wf.export_id:
            workflow.export_id = incoming_wf.export_id
        # Its previous steps (and their private vessels) are superseded by the
        # file's; clear the run pointers so nothing dangles into the old shape.
        await _clear_steps(session, workflow)
        workflow.current_step_id = None
    else:
        workflow = Workflow(
            name=await _unique_workflow_name(session, incoming_wf.name),
            # A fresh copy is its own object — see the agent case above.
            export_id=None if wf_action == "create" else (incoming_wf.export_id or None),
            status="draft",
        )
        session.add(workflow)

    workflow.description = incoming_wf.description
    workflow.icon = incoming_wf.icon
    workflow.color = incoming_wf.color
    workflow.clear_artifacts = incoming_wf.clear_artifacts
    workflow.max_loops = incoming_wf.max_loops
    workflow.step_timeout_seconds = incoming_wf.step_timeout_seconds
    workflow.role_id = role_id
    if incoming_wf.schedule is not None:
        # Imported paused, like an agent's cadence.
        workflow.schedule_enabled = False
        workflow.interval_seconds = incoming_wf.schedule.interval_seconds
        workflow.run_at_minute = incoming_wf.schedule.run_at_minute
        workflow.timezone = incoming_wf.schedule.timezone
        workflow.days_of_week = incoming_wf.schedule.days_of_week
        workflow.next_run_at = None
    await session.flush()

    for pos, step in enumerate(incoming_wf.steps):
        agent = agent_rows[step.agent] if step.agent is not None else None
        session.add(
            WorkflowStep(
                workflow_id=workflow.id,
                agent_id=agent.id if (agent is not None and step.kind != "approval") else None,
                position=pos,
                name=step.name,
                kind=step.kind if step.kind in WORKFLOW_STEP_KINDS else "task",
                on_fail_position=step.on_fail_position,
                instructions=(step.instructions or "").strip() or None,
                on_error=(
                    step.on_error if step.on_error in WORKFLOW_STEP_ERROR_POLICIES else "fail"
                ),
                max_retries=step.max_retries,
                on_reject=(
                    step.on_reject if step.on_reject in WORKFLOW_STEP_REJECT_POLICIES else "rework"
                ),
                context_mode=(
                    step.context_mode
                    if step.context_mode in WORKFLOW_STEP_CONTEXT_MODES
                    else "auto"
                ),
                context_sources=(step.context_sources or "").strip() or None,
                use_mcp=step.use_mcp,
                use_skills=step.use_skills,
                use_memory=step.use_memory,
                # Kept verbatim, including an explicit empty string: null and
                # empty are different scopes (all enabled servers vs none).
                mcp_servers=step.mcp_servers,
            )
        )
    workflow.status = "idle" if incoming_wf.steps else "draft"
    await session.commit()

    result.workflow_id = workflow.id
    result.name = workflow.name
    return result


async def _clear_steps(session: AsyncSession, workflow: Workflow) -> None:
    """Drop a replaced workflow's steps and the private vessels they owned."""
    existing = (
        (await session.execute(select(WorkflowStep).where(WorkflowStep.workflow_id == workflow.id)))
        .scalars()
        .all()
    )
    vessels: list[int] = []
    for step in existing:
        if step.agent_id is not None:
            owned = await session.get(AgentSession, step.agent_id)
            if owned is not None and owned.inline:
                vessels.append(owned.id)
        await session.delete(step)
    await session.flush()
    for agent_id in vessels:
        orphan = await session.get(AgentSession, agent_id)
        if orphan is not None and orphan.inline:
            await session.delete(orphan)
    await session.flush()


async def load_workflow(session: AsyncSession, workflow_id: int) -> Workflow:
    result = await session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.steps).selectinload(WorkflowStep.agent))
        .execution_options(populate_existing=True)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return workflow
