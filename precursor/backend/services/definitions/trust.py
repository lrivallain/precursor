"""Files mode: permission changes made on disk are held until a human accepts them.

A definition file decides what an agent may do — its approval policy, whether
it runs autonomously and for how long, which tools and MCP servers it sees, how
much it may spend. Once files can arrive from a ``git pull``, a teammate, or a
tool writing to disk, a change that *widens* any of that must not take effect
silently. Each row keeps the permissions a human last accepted
(``accepted_permissions``); a file that goes beyond them can't start a run
until someone reviews and accepts the difference in the app.

- Precursor itself records the accepted snapshot whenever it writes a file (the
  export, a save from the app): those values came from the user.
- A file new to this instance (adopted from disk) starts with nothing accepted.
- A row with no snapshot yet falls back to its database columns — the values
  last set in the app.
- Narrowing never needs review.

This gates permissions, not content: a changed prompt within the permissions
already accepted is not held.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AgentSession, Workflow
from precursor.backend.schemas.definitions import AgentDefinition, WorkflowDefinition
from precursor.backend.services.definitions import overlay

# Stored for a row adopted from a file nobody accepted yet: every permission the
# file grants then counts as new.
NOTHING_ACCEPTED = "{}"

_POLICY_RANK = {"manual": 0, "balanced": 1, "autonomous": 2}
_TRI_RANK = {False: 0, None: 1, True: 2}


# --- Snapshots --------------------------------------------------------------


def agent_permissions(defn: AgentDefinition) -> dict[str, Any]:
    caps = defn.capabilities
    return {
        "approval_policy": defn.approval_policy,
        "autonomy": defn.autonomy.enabled,
        "max_steps": defn.autonomy.max_steps,
        "mcp": caps.mcp,
        "skills": caps.skills,
        "memory": caps.memory,
        "mcp_servers": caps.mcp_servers,
        "token_budget": defn.limits.token_budget,
    }


def workflow_permissions(defn: WorkflowDefinition) -> dict[str, Any]:
    return {
        "approval_policy": defn.approval_policy,
        # Order and kinds: a human approval only guards the steps after it.
        "order": [step.key for step in defn.steps],
        "steps": {
            step.key: {
                "kind": step.kind,
                "agent": step.agent,
                "prompt": step.prompt is not None,
                "mcp": step.capabilities.mcp,
                "skills": step.capabilities.skills,
                "memory": step.capabilities.memory,
                "mcp_servers": step.capabilities.mcp_servers,
            }
            for step in defn.steps
        },
    }


def _servers(raw: str | None) -> list[str] | None:
    return None if raw is None else [s.strip() for s in raw.split(",") if s.strip()]


# --- Widening ---------------------------------------------------------------


def _policy_widened(old: str | None, new: str | None) -> bool:
    # ``None`` inherits a default that may be anything, so only a move to
    # "manual", or down between two explicit policies, is known to be narrower.
    if new == old or new == "manual":
        return False
    return not (
        old in _POLICY_RANK and new in _POLICY_RANK and _POLICY_RANK[new] < _POLICY_RANK[old]
    )


def _scope_widened(old: list[str] | None, new: list[str] | None) -> list[str] | None:
    """Servers ``new`` adds over ``old``; ``None`` (every server) is the widest."""
    if new is None:
        return [] if old is not None else None
    if old is None:
        return None
    added = [s for s in new if s not in old]
    return added or None


def _agent_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    if _policy_widened(old.get("approval_policy", "manual"), new["approval_policy"]):
        changes.append(
            f"approval policy → {new['approval_policy'] or 'inherit the global default'}"
        )
    if new["autonomy"] and not old.get("autonomy", False):
        changes.append("runs autonomously")
    if new["autonomy"] and new["max_steps"] > old.get("max_steps", 0):
        changes.append(f"up to {new['max_steps']} autonomous steps")
    for key, label in (("mcp", "MCP tools"), ("skills", "skills"), ("memory", "memory")):
        if new[key] and not old.get(key, False):
            changes.append(f"can use {label}")
    if new["mcp"]:
        added = _scope_widened(old.get("mcp_servers", []), new["mcp_servers"])
        if added is not None:
            changes.append(
                "can reach every enabled MCP server"
                if not added
                else f"can reach MCP server {', '.join(added)}"
            )
    old_budget = old.get("token_budget", 0)
    budget = new["token_budget"]
    if old_budget is not None and (budget is None or budget > old_budget):
        changes.append(
            "no token budget" if budget is None else f"token budget raised to {budget:,}"
        )
    return changes


def _workflow_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    if _policy_widened(old.get("approval_policy", "manual"), new["approval_policy"]):
        changes.append(
            f"approval policy for every step → {new['approval_policy'] or "each agent's own"}"
        )
    old_steps: dict[str, Any] = old.get("steps", {})
    changes += _approvals_weakened(old, new)
    for key, step in new["steps"].items():
        before = old_steps.get(key)
        if before is None:
            changes.append(f"adds step '{key}'")
            continue
        if step["agent"] != before.get("agent") or step["prompt"] != before.get("prompt"):
            what = step["agent"] or "its own prompt"
            changes.append(f"step '{key}' now runs {what}")
        for cap, label in (("mcp", "MCP tools"), ("skills", "skills"), ("memory", "memory")):
            if _TRI_RANK[step[cap]] > _TRI_RANK[before.get(cap, False)]:
                changes.append(f"step '{key}' can use {label}")
        if step["mcp"] is not False:
            added = _scope_widened(before.get("mcp_servers", []), step["mcp_servers"])
            if added is not None:
                changes.append(
                    f"step '{key}' can reach every enabled MCP server"
                    if not added
                    else f"step '{key}' can reach MCP server {', '.join(added)}"
                )
    return changes


def _approvals_weakened(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """A human checkpoint removed, or steps moved out from behind one."""
    old_order: list[str] | None = old.get("order")
    if not old_order:
        return []
    old_steps: dict[str, Any] = old.get("steps", {})
    new_order: list[str] = new["order"]
    changes: list[str] = []
    for index, key in enumerate(old_order):
        if old_steps.get(key, {}).get("kind") != "approval":
            continue
        if key not in new["steps"] or new["steps"][key]["kind"] != "approval":
            changes.append(f"removes approval step '{key}'")
            continue
        at = new_order.index(key)
        slipped = [k for k in old_order[index + 1 :] if k in new_order and new_order.index(k) < at]
        if slipped:
            names = ", ".join(f"'{k}'" for k in slipped)
            changes.append(f"step {names} no longer waits for approval '{key}'")
    return changes


# --- Rows -------------------------------------------------------------------


def _current(row: AgentSession | Workflow) -> dict[str, Any] | None:
    """The permissions the row's file grants now; ``None`` if it has no usable file."""
    linked = overlay.linked_file(row)
    if linked is None:
        return None
    if isinstance(row, Workflow) and isinstance(linked.definition, WorkflowDefinition):
        return workflow_permissions(linked.definition)
    if isinstance(row, AgentSession) and isinstance(linked.definition, AgentDefinition):
        return agent_permissions(linked.definition)
    return None


async def _database_baseline(session: AsyncSession, row: AgentSession | Workflow) -> dict[str, Any]:
    """The columns last set in the app, read around the file projection."""
    if isinstance(row, Workflow):
        policy = (
            await session.execute(
                select(Workflow.__table__.c.approval_policy).where(
                    Workflow.__table__.c.id == row.id
                )
            )
        ).scalar_one_or_none()
        # The database's step rows can't vouch for the file's steps (rows the
        # file added were filled from it), so none count as accepted: such a
        # workflow is reviewed once. The export records a snapshot, so this
        # only concerns rows linked some other way.
        return {"approval_policy": policy, "steps": {}}
    table = AgentSession.__table__
    raw = (
        await session.execute(
            select(
                table.c.approval_policy,
                table.c.autonomy_enabled,
                table.c.max_steps,
                table.c.use_mcp,
                table.c.use_skills,
                table.c.use_memory,
                table.c.mcp_servers,
                table.c.token_budget,
            ).where(table.c.id == row.id)
        )
    ).one_or_none()
    if raw is None:
        return {}
    return {
        "approval_policy": raw[0],
        "autonomy": bool(raw[1]),
        "max_steps": raw[2],
        "mcp": bool(raw[3]),
        "skills": bool(raw[4]),
        "memory": bool(raw[5]),
        "mcp_servers": _servers(raw[6]),
        "token_budget": raw[7],
    }


def permissions_of(defn: AgentDefinition | WorkflowDefinition) -> dict[str, Any]:
    if isinstance(defn, WorkflowDefinition):
        return workflow_permissions(defn)
    return agent_permissions(defn)


async def accepted_for(session: AsyncSession, row: AgentSession | Workflow) -> dict[str, Any]:
    """The permissions last accepted for ``row``."""
    if row.accepted_permissions is None:
        if overlay.finalized() is not None:
            # The columns were cleaned up: they vouch for nothing any more.
            return {}
        return await _database_baseline(session, row)
    try:
        return json.loads(row.accepted_permissions) or {}
    except ValueError:
        return {}


def changes_between(
    row: AgentSession | Workflow, accepted: dict[str, Any], current: dict[str, Any]
) -> list[str]:
    if isinstance(row, Workflow):
        return _workflow_changes(accepted, current)
    return _agent_changes(accepted, current)


async def pending_changes(
    session: AsyncSession,
    row: AgentSession | Workflow,
    definition: AgentDefinition | WorkflowDefinition | None = None,
) -> list[str]:
    """What the row's file grants beyond what was accepted; empty when nothing.

    ``definition`` checks that version instead of the file as currently
    scanned — e.g. the exact bytes a run is about to pin.
    """
    if not overlay.files_mode() or (isinstance(row, AgentSession) and row.inline):
        return []
    current = permissions_of(definition) if definition is not None else _current(row)
    if current is None:
        return []
    return changes_between(row, await accepted_for(session, row), current)


def record(row: AgentSession | Workflow, defn: AgentDefinition | WorkflowDefinition) -> None:
    """Accept ``defn``'s permissions for ``row`` (the caller commits)."""
    snapshot = (
        workflow_permissions(defn)
        if isinstance(defn, WorkflowDefinition)
        else agent_permissions(defn)
    )
    row.accepted_permissions = json.dumps(snapshot, sort_keys=True)


def accept_current(row: AgentSession | Workflow) -> bool:
    """Accept what the row's file grants now; ``False`` if it has no usable file."""
    linked = overlay.linked_file(row)
    if linked is None or linked.definition is None:
        return False
    record(row, linked.definition)
    return True


def review_message(name: str, changes: list[str]) -> str:
    return (
        f"'{name}' has permission changes waiting for review ({'; '.join(changes)}). "
        "Accept them in the app to run it."
    )
