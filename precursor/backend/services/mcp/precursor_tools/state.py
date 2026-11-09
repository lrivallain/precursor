"""Agent scratchpad (``state_*``) and workflow shared-state (``workflow_state_*``) tools."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentSession,
    Workflow,
    WorkflowStep,
)

if TYPE_CHECKING:
    pass
from precursor.backend.services.mcp.precursor_tools._core import _GATED, _iso, _section_enabled, mcp


# --------------------------------------------------------------------------
# agent_state (read/write) — an agent's private scratchpad across runs
# --------------------------------------------------------------------------
def _self_agent_id() -> int | None:
    """The agent this server was launched for, when it was launched by one.

    ``AgentManager`` stamps ``PRECURSOR_AGENT_ID`` into the stdio subprocess env
    so an agent can call ``state_set("cursor", …)`` without knowing its own row
    id. An external MCP host gets no default and must pass ``agent_id``.
    """
    raw = os.environ.get("PRECURSOR_AGENT_ID")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


async def _resolve_state_agent(agent_id: int | None) -> tuple[int | None, str | None]:
    """Resolve + validate the target agent. Returns ``(id, error)``."""
    resolved = agent_id if agent_id is not None else _self_agent_id()
    if resolved is None:
        return None, (
            "No agent in context. Pass agent_id explicitly (see list_agents) — "
            "the implicit default only exists inside an agent session."
        )
    async with SessionLocal() as session:
        exists = await session.get(AgentSession, resolved)
    if exists is None:
        return None, f"Agent {resolved} not found"
    return resolved, None


@mcp.tool()
async def state_list(agent_id: int | None = None) -> dict[str, Any]:
    """List the keys an agent has saved in its durable state — **keys only**.

    This is the agent's private scratchpad: unlike artifacts (wiped on every
    fresh run) and memories (global, injected everywhere), state survives
    re-runs and belongs to one agent. Bodies are omitted on purpose — call
    ``state_get`` for the one you need.

    ``agent_id`` defaults to the calling agent when running inside a session.
    """
    if not await _section_enabled("agent_state"):
        return {"error": _GATED.format(section="agent_state")}
    from precursor.backend.services import agent_state as state_service

    resolved, error = await _resolve_state_agent(agent_id)
    if error is not None:
        return {"error": error}
    async with SessionLocal() as session:
        entries = await state_service.list_state_keys(session, resolved)  # type: ignore[arg-type]
    return {
        "agent_id": resolved,
        "keys": [{"key": e.key, "size": e.size, "updated_at": _iso(e.updated_at)} for e in entries],
        "count": len(entries),
    }


@mcp.tool()
async def state_get(key: str, agent_id: int | None = None) -> dict[str, Any]:
    """Read one durable state entry saved by a previous run.

    Returns ``found: false`` (not an error) when the key was never written, so a
    first run can branch on it without special-casing failures.
    """
    if not await _section_enabled("agent_state"):
        return {"error": _GATED.format(section="agent_state")}
    from precursor.backend.services import agent_state as state_service

    resolved, error = await _resolve_state_agent(agent_id)
    if error is not None:
        return {"error": error}
    async with SessionLocal() as session:
        row = await state_service.get_state(session, resolved, key.strip().lower())  # type: ignore[arg-type]
    if row is None:
        return {"agent_id": resolved, "key": key, "found": False}
    return {
        "agent_id": resolved,
        "key": row.key,
        "value": row.value,
        "found": True,
        "updated_at": _iso(row.updated_at),
    }


@mcp.tool()
async def state_set(key: str, value: str, agent_id: int | None = None) -> dict[str, Any]:
    """Save (upsert) a durable state entry that survives this agent's re-runs.

    Use it for cross-run bookkeeping — a cursor, the ids you already processed,
    a counter, a digest of the payload you last acted on — so the next run
    resumes instead of redoing work. ``value`` is opaque text; JSON is the
    convention. Writing an existing ``key`` replaces it.

    Keep bodies small: this is for bookkeeping, not payloads. Park large or
    binary content in a workspace file and store the path here instead.
    """
    if not await _section_enabled("agent_state"):
        return {"error": _GATED.format(section="agent_state")}
    from precursor.backend.schemas import AgentStateWrite
    from precursor.backend.services import agent_state as state_service

    resolved, error = await _resolve_state_agent(agent_id)
    if error is not None:
        return {"error": error}
    try:
        payload = AgentStateWrite(key=key, value=value)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        try:
            row, created = await state_service.set_state(session, resolved, payload)  # type: ignore[arg-type]
        except ValueError as exc:
            return {"error": str(exc)}
    return {"agent_id": resolved, "key": row.key, "created": created, "size": len(row.value)}


@mcp.tool()
async def state_delete(key: str, agent_id: int | None = None) -> dict[str, Any]:
    """Delete one durable state entry. Returns ``deleted: false`` if absent."""
    if not await _section_enabled("agent_state"):
        return {"error": _GATED.format(section="agent_state")}
    from precursor.backend.services import agent_state as state_service

    resolved, error = await _resolve_state_agent(agent_id)
    if error is not None:
        return {"error": error}
    async with SessionLocal() as session:
        removed = await state_service.delete_state(session, resolved, key.strip().lower())  # type: ignore[arg-type]
    return {"agent_id": resolved, "key": key, "deleted": removed}


# --------------------------------------------------------------------------
# workflow_state (read/write) — a pipeline's memory, shared across its steps
# --------------------------------------------------------------------------
async def _resolve_workflow(workflow_id: int | None) -> tuple[int | None, str | None]:
    """Resolve + validate the target workflow. Returns ``(id, error)``.

    With no explicit id this answers "which pipeline am I a step of right now?"
    — the running workflow whose *current step* is the calling agent. Resolved
    per call rather than baked into the subprocess env like
    ``PRECURSOR_AGENT_ID``: a step's agent is a **reusable** row that several
    workflows may reference, and its SDK session outlives any one run, so the
    owning pipeline is a property of the moment, not of the session.
    """
    if workflow_id is not None:
        async with SessionLocal() as session:
            exists = await session.get(Workflow, workflow_id)
        if exists is None:
            return None, f"Workflow {workflow_id} not found"
        return workflow_id, None

    agent_id = _self_agent_id()
    if agent_id is None:
        return None, (
            "No workflow in context. Pass workflow_id explicitly — the implicit "
            "default only exists while an agent is running as a workflow step."
        )
    async with SessionLocal() as session:
        found = (
            (
                await session.execute(
                    select(Workflow.id)
                    .join(WorkflowStep, Workflow.current_step_id == WorkflowStep.id)
                    .where(Workflow.status == "running", WorkflowStep.agent_id == agent_id)
                )
            )
            .scalars()
            .first()
        )
    if found is None:
        return None, (
            "This agent is not currently running as a step of any workflow. Pass "
            "workflow_id explicitly if you meant a specific pipeline."
        )
    return found, None


@mcp.tool()
async def workflow_state_list(workflow_id: int | None = None) -> dict[str, Any]:
    """List the keys a workflow keeps in its saved state — **keys only**.

    Workflow state is the pipeline's own memory: named values that persist
    **across runs** and are shared by every step, so one step can leave a fact
    for a later step (or for the next run) to pick up. Distinct from your own
    agent state, which is private to you and follows you between pipelines.

    ``workflow_id`` defaults to the workflow currently running you as a step.
    """
    if not await _section_enabled("workflow_state"):
        return {"error": _GATED.format(section="workflow_state")}
    from precursor.backend.services import workflow_state as state_service

    resolved, error = await _resolve_workflow(workflow_id)
    if error is not None:
        return {"error": error}
    async with SessionLocal() as session:
        entries = await state_service.list_state_keys(session, resolved)  # type: ignore[arg-type]
    return {
        "workflow_id": resolved,
        "keys": [{"key": e.key, "size": e.size, "updated_at": _iso(e.updated_at)} for e in entries],
        "count": len(entries),
    }


@mcp.tool()
async def workflow_state_get(key: str, workflow_id: int | None = None) -> dict[str, Any]:
    """Read one value from the workflow's saved state.

    Returns ``found: false`` (not an error) when the key was never written, so a
    first run can branch on it without special-casing failures.
    """
    if not await _section_enabled("workflow_state"):
        return {"error": _GATED.format(section="workflow_state")}
    from precursor.backend.services import workflow_state as state_service

    resolved, error = await _resolve_workflow(workflow_id)
    if error is not None:
        return {"error": error}
    async with SessionLocal() as session:
        row = await state_service.get_state(session, resolved, key.strip().lower())  # type: ignore[arg-type]
    if row is None:
        return {"workflow_id": resolved, "key": key, "found": False}
    return {
        "workflow_id": resolved,
        "key": row.key,
        "value": row.value,
        "found": True,
        "updated_at": _iso(row.updated_at),
    }


@mcp.tool()
async def workflow_state_set(
    key: str, value: str, workflow_id: int | None = None
) -> dict[str, Any]:
    """Save (upsert) a value into the workflow's saved state.

    Use it to hand a *named* fact to a later step — which can read it back with
    ``workflow_state_get`` or have it substituted straight into its instructions
    via a ``{{state.<key>}}`` placeholder — or to leave a cursor for the pipeline's
    **next run** so it resumes instead of redoing work. Writing an existing
    ``key`` replaces it.

    ``value`` is opaque text; JSON is the convention. Keep it small: this is
    bookkeeping, not a place to park a document (publish that as an artifact).
    """
    if not await _section_enabled("workflow_state"):
        return {"error": _GATED.format(section="workflow_state")}
    from precursor.backend.schemas.workflow_state import WorkflowStateWrite
    from precursor.backend.services import workflow_state as state_service

    resolved, error = await _resolve_workflow(workflow_id)
    if error is not None:
        return {"error": error}
    try:
        payload = WorkflowStateWrite(key=key, value=value)
    except ValueError as exc:
        return {"error": str(exc)}
    async with SessionLocal() as session:
        try:
            row, created = await state_service.set_state(session, resolved, payload)  # type: ignore[arg-type]
        except ValueError as exc:
            return {"error": str(exc)}
    return {"workflow_id": resolved, "key": row.key, "created": created, "size": len(row.value)}


@mcp.tool()
async def workflow_state_delete(key: str, workflow_id: int | None = None) -> dict[str, Any]:
    """Delete one key from the workflow's saved state."""
    if not await _section_enabled("workflow_state"):
        return {"error": _GATED.format(section="workflow_state")}
    from precursor.backend.services import workflow_state as state_service

    resolved, error = await _resolve_workflow(workflow_id)
    if error is not None:
        return {"error": error}
    async with SessionLocal() as session:
        removed = await state_service.delete_state(session, resolved, key.strip().lower())  # type: ignore[arg-type]
    return {"workflow_id": resolved, "key": key, "deleted": removed}
