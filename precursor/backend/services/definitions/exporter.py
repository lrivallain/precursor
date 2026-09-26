"""One-shot export of the database's agents and workflows into definition files.

Roadmap step 3: the database is still authoritative, and nothing is deleted.
Each exported row is linked to its file through ``export_id`` — the portable
identity transfer files already use — which becomes the file's ``id``.

- A reusable agent → ``agents/<slug>.agent.yaml``; one spawned from a topic or
  chat → ``agents/adhoc/<slug>.agent.yaml``.
- A workflow → ``workflows/<slug>.workflow.yaml``. A step's private (inline)
  agent gets no file: its prompt and model move into the step.
- A row that already has a file is left alone unless ``overwrite`` is set, in
  which case the same file is rewritten in place.

Settings the format can't hold, or values it would reject, are normalised with
a warning. A workflow the format can't express at all (a step whose agent was
deleted) is still written, as-is, and reported as an error to fix by hand —
dropping the step would shift every position-based reference after it.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from precursor.backend.models import AgentSession, Role, Workflow, WorkflowStep
from precursor.backend.schemas.definitions import (
    AGENT_FILE_SUFFIX,
    WORKFLOW_FILE_SUFFIX,
    AgentDefinition,
    WorkflowDefinition,
)
from precursor.backend.schemas.definitions_api import (
    DefinitionIssue,
    DefinitionKind,
    DefinitionsExportResult,
    ExportedDefinition,
)
from precursor.backend.services.definitions import overlay, trust
from precursor.backend.services.definitions.loader import DefinitionSet, load_definitions
from precursor.backend.services.slugs import slugify
from precursor.backend.services.workflow_state import keyed_step_references

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_APPROVAL_POLICIES = ("manual", "balanced", "autonomous")
_HEADER = "# Keep `id` unchanged: it links this file to its run history in Precursor.\n"


# --- YAML output ------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    """Readable, hand-editable output: indented lists, prompts as ``|`` blocks."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    # PyYAML falls back to a quoted style on its own when a block can't
    # represent the text exactly (e.g. trailing spaces).
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)


def render_document(doc: dict[str, Any]) -> str:
    body = yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    return _HEADER + body


def _write_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=target.parent)
    try:
        # ``newline=""``: the bytes on disk are exactly ``text``, so a hash of it
        # is the file's hash on every platform.
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- Names ------------------------------------------------------------------


def _short_slug(text: str, limit: int) -> str:
    slug = slugify(text)
    if len(slug) <= limit:
        return slug
    cut = slug[:limit]
    if slug[limit] != "-" and "-" in cut:
        cut = cut.rsplit("-", 1)[0]
    return cut.strip("-")


@dataclass
class _Plan:
    """Paths and ids handed out during one export, so nothing collides."""

    taken_paths: set[str] = field(default_factory=set)
    ids_by_kind: dict[str, set[str]] = field(default_factory=dict)
    # Column values to set on each exported row: its minted id, the accepted
    # permissions. Keyed by (table, id) so both land in one update.
    links: dict[
        tuple[str, int], tuple[type[AgentSession] | type[Workflow], int, dict[str, Any]]
    ] = field(default_factory=dict)

    def link(self, row: AgentSession | Workflow, values: dict[str, Any]) -> None:
        key = (type(row).__name__, row.id)
        _, _, current = self.links.setdefault(key, (type(row), row.id, {}))
        current.update(values)

    def claim_path(self, root: Path, folder: str, slug: str, suffix: str) -> str:
        n = 1
        while True:
            name = slug if n == 1 else f"{slug}-{n}"
            candidate = f"{folder}/{name}{suffix}"
            # Compare case-insensitively: macOS and Windows filesystems are.
            if candidate.lower() not in self.taken_paths and not (root / candidate).exists():
                self.taken_paths.add(candidate.lower())
                return candidate
            n += 1


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _server_list(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    out: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name and name not in out:
            out.append(name)
    return out


# --- Documents --------------------------------------------------------------


@dataclass
class _Context:
    root: Path
    roles: dict[int, str]
    result: DefinitionsExportResult

    def warn(self, path: str | None, message: str, location: str | None = None) -> None:
        self.result.issues.append(
            DefinitionIssue(severity="warning", path=path, location=location, message=message)
        )


def _agent_document(ctx: _Context, agent: AgentSession, ident: str, path: str) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "agent",
        "id": ident,
        "title": (agent.title or "").strip()[:200] or "Agent",
    }
    if (agent.task_prompt or "").strip():
        doc["prompt"] = agent.task_prompt
    if agent.model:
        doc["model"] = agent.model
    if agent.role_id is not None and agent.role_id in ctx.roles:
        doc["role"] = ctx.roles[agent.role_id]
    if agent.approval_policy in _APPROVAL_POLICIES:
        doc["approval_policy"] = agent.approval_policy
    elif agent.approval_policy:
        ctx.warn(path, f"unknown approval policy '{agent.approval_policy}' left out")

    autonomy: dict[str, Any] = {}
    if agent.autonomy_enabled:
        autonomy["enabled"] = True
    max_steps = _clamp(agent.max_steps, 1, 100)
    if max_steps != agent.max_steps:
        ctx.warn(path, f"max_steps {agent.max_steps} clamped to {max_steps}", "autonomy.max_steps")
    if max_steps != 12:
        autonomy["max_steps"] = max_steps
    if autonomy:
        doc["autonomy"] = autonomy

    caps: dict[str, Any] = {}
    if not agent.use_mcp:
        caps["mcp"] = False
    if not agent.use_skills:
        caps["skills"] = False
    if not agent.use_memory:
        caps["memory"] = False
    servers = _server_list(agent.mcp_servers)
    # With MCP off the allowlist can't matter, and the format rejects the pair.
    if servers is not None and agent.use_mcp:
        caps["mcp_servers"] = servers
    if caps:
        doc["capabilities"] = caps

    limits: dict[str, Any] = {}
    if agent.token_budget is not None and agent.token_budget > 0:
        limits["token_budget"] = agent.token_budget
    max_retries = _clamp(agent.max_retries, 0, 10)
    if max_retries != agent.max_retries:
        ctx.warn(path, f"max_retries {agent.max_retries} clamped to {max_retries}", "limits")
    if max_retries:
        limits["max_retries"] = max_retries
    if limits:
        doc["limits"] = limits
    return doc


def _step_keys(steps: list[WorkflowStep], preferred: list[str | None] | None = None) -> list[str]:
    """One unique key per step: the ``preferred`` one when given (an in-app
    save keeps the keys the file already had), else derived from its label."""
    keys: list[str] = []
    wanted = preferred or []
    taken = {k for k in wanted if k}
    for pos, step in enumerate(steps):
        keep = wanted[pos] if pos < len(wanted) else None
        if keep and keep not in keys:
            keys.append(keep)
            continue
        label = step.name or (step.agent.title if step.agent is not None else "") or ""
        fallback = "step" if step.kind in ("task", "inline") else step.kind
        base = _short_slug(label, 32) or f"{fallback}-{pos + 1}"
        key, n = base, 2
        while key in keys or key in taken:
            key = f"{base}-{n}"
            n += 1
        keys.append(key)
    return keys


def _vessel_settings_lost(vessel: AgentSession, workflow: Workflow) -> list[str]:
    """Settings of a step's hidden agent that would stop applying once it's a prompt.

    The workflow's own role and approval policy replace the agent's for every
    step while it runs, so the agent's copy only matters when the workflow sets
    none.
    """
    lost = []
    if vessel.role_id is not None and workflow.role_id is None:
        lost.append("role")
    if vessel.approval_policy and workflow.approval_policy is None:
        lost.append("approval policy")
    if vessel.autonomy_enabled or vessel.max_steps != 12:
        lost.append("autonomy")
    if vessel.token_budget is not None or vessel.max_retries:
        lost.append("limits")
    return lost


def _step_document(
    ctx: _Context,
    path: str,
    workflow: Workflow,
    steps: list[WorkflowStep],
    keys: list[str],
    pos: int,
    agent_paths: dict[int, str],
) -> dict[str, Any]:
    step = steps[pos]
    key = keys[pos]
    where = f"steps[{key}]"
    kind = step.kind if step.kind in ("task", "inline", "gate", "approval") else "task"
    doc: dict[str, Any] = {"key": key}
    if step.name:
        doc["name"] = step.name

    agent = step.agent if kind != "approval" else None
    effective_kind = kind
    if agent is not None and not agent.inline and kind == "inline":
        # "inline" with a reusable agent is a step that minted a listed agent;
        # it runs exactly like a task step pointing at that agent.
        effective_kind = "task"
    if effective_kind != "task":
        doc["kind"] = effective_kind

    if agent is not None:
        if agent.inline:
            doc["prompt"] = agent.task_prompt or ""
            if agent.model:
                doc["model"] = agent.model
            if lost := _vessel_settings_lost(agent, workflow):
                ctx.warn(
                    path,
                    f"the step's own agent had settings a step can't hold ({', '.join(lost)}); "
                    "they were left out",
                    where,
                )
        else:
            doc["agent"] = agent_paths[agent.id]

    if kind in ("gate", "approval") and step.on_fail_position is not None:
        target = step.on_fail_position
        # The engine falls back to the previous step for any target it can't
        # drive into, so leaving such a target out keeps the behaviour.
        if 0 <= target < len(steps) and target != pos:
            runnable = steps[target].kind == "approval" or steps[target].agent_id is not None
            if runnable:
                doc["on_fail"] = keys[target]

    instructions = (step.instructions or "").strip()
    if instructions:
        # By key in the file, so the reference survives a reorder there.
        doc["instructions"] = keyed_step_references(instructions, keys)

    if kind == "approval":
        if step.on_reject in ("stop", "skip"):
            doc["on_reject"] = step.on_reject
        return doc

    if step.on_error in ("retry", "continue"):
        doc["on_error"] = step.on_error
        if step.on_error == "retry" and step.max_retries > 0:
            doc["max_retries"] = _clamp(step.max_retries, 0, 10)

    mode = step.context_mode if step.context_mode in ("auto", "selected", "none") else "auto"
    if mode == "none":
        doc["context"] = {"mode": "none"}
    elif mode == "selected":
        wanted: list[int] = []
        ignored: list[str] = []
        for chunk in (step.context_sources or "").replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk.isdigit():
                continue
            src = int(chunk)
            if 0 <= src < pos:
                if src not in wanted:
                    wanted.append(src)
            else:
                ignored.append(str(src + 1))
        if ignored:
            ctx.warn(
                path,
                f"context sources {', '.join(ignored)} are not earlier steps and were left out",
                f"{where}.context",
            )
        if wanted:
            doc["context"] = {"mode": "selected", "from": [keys[i] for i in sorted(wanted)]}
        else:
            ctx.warn(
                path,
                "context 'selected' named no earlier step, so the step inherited nothing; "
                "written as mode 'none'",
                f"{where}.context",
            )
            doc["context"] = {"mode": "none"}

    caps: dict[str, Any] = {}
    vessel = agent if agent is not None and agent.inline else None
    for field_name, key_name in (
        ("use_mcp", "mcp"),
        ("use_skills", "skills"),
        ("use_memory", "memory"),
    ):
        value = getattr(step, field_name)
        # A step's own prompt has no agent file to inherit from, so a toggle its
        # hidden agent had switched off must now be stated on the step.
        if value is None and vessel is not None and not getattr(vessel, field_name):
            value = False
        if value is not None:
            caps[key_name] = value
    # Unlike the toggles, the engine takes a step's server scope as-is, null
    # included, so the agent's own list never applies inside a workflow.
    servers = _server_list(step.mcp_servers)
    if servers is not None and caps.get("mcp") is not False:
        caps["mcp_servers"] = servers
    if caps:
        doc["capabilities"] = caps
    return doc


def _workflow_document(
    ctx: _Context,
    workflow: Workflow,
    ident: str,
    path: str,
    agent_paths: dict[int, str],
    *,
    steps: list[WorkflowStep] | None = None,
    preferred_keys: list[str | None] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "workflow",
        "id": ident,
        "name": (workflow.name or "").strip()[:200] or "Workflow",
    }
    if (workflow.description or "").strip():
        doc["description"] = workflow.description
    if workflow.icon:
        doc["icon"] = workflow.icon
    if workflow.color:
        doc["color"] = workflow.color
    if workflow.role_id is not None and workflow.role_id in ctx.roles:
        doc["role"] = ctx.roles[workflow.role_id]
    if workflow.approval_policy in _APPROVAL_POLICIES:
        doc["approval_policy"] = workflow.approval_policy
    elif workflow.approval_policy:
        ctx.warn(path, f"unknown approval policy '{workflow.approval_policy}' left out")
    if not workflow.clear_artifacts:
        doc["clear_artifacts"] = False
    max_loops = _clamp(workflow.max_loops, 1, 25)
    if max_loops != workflow.max_loops:
        ctx.warn(path, f"max_loops {workflow.max_loops} clamped to {max_loops}", "max_loops")
    if max_loops != 3:
        doc["max_loops"] = max_loops
    if workflow.step_timeout_seconds:
        timeout = _clamp(workflow.step_timeout_seconds, 30, 86400)
        if timeout != workflow.step_timeout_seconds:
            ctx.warn(
                path,
                f"step_timeout_seconds {workflow.step_timeout_seconds} clamped to {timeout}",
                "step_timeout_seconds",
            )
        doc["step_timeout_seconds"] = timeout

    steps = sorted(steps if steps is not None else workflow.steps, key=lambda s: s.position)
    keys = _step_keys(steps, preferred_keys)
    doc["steps"] = [
        _step_document(ctx, path, workflow, steps, keys, pos, agent_paths)
        for pos in range(len(steps))
    ]
    return doc


def _report_invalid(
    ctx: _Context, kind: DefinitionKind, path: str, doc: dict[str, Any]
) -> AgentDefinition | WorkflowDefinition | None:
    """The validated document, or ``None`` after reporting why it isn't valid."""
    model: type[AgentDefinition] | type[WorkflowDefinition] = (
        AgentDefinition if kind == "agent" else WorkflowDefinition
    )
    try:
        return model.model_validate(doc)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or None
            ctx.result.issues.append(
                DefinitionIssue(
                    severity="error",
                    path=path,
                    location=loc,
                    message=err["msg"].removeprefix("Value error, ")
                    + " — written as-is; fix it by hand",
                )
            )
        return None


def _accepted(defn: AgentDefinition | WorkflowDefinition | None) -> dict[str, str]:
    """The permission snapshot to record for a file Precursor wrote itself.

    What it writes comes from the database, i.e. from what was set in the app,
    so those permissions are the accepted ones.
    """
    if isinstance(defn, WorkflowDefinition):
        snapshot = trust.workflow_permissions(defn)
    elif isinstance(defn, AgentDefinition):
        snapshot = trust.agent_permissions(defn)
    else:
        return {}
    return {"accepted_permissions": json.dumps(snapshot, sort_keys=True)}


# --- Export -----------------------------------------------------------------


def _existing_ids(dset: DefinitionSet) -> dict[str, dict[str, str]]:
    """``{kind: {id: path}}`` for every file whose id could be read."""
    out: dict[str, dict[str, str]] = {"agent": {}, "workflow": {}}
    for f in dset.files:
        if f.raw_id:
            out[f.kind].setdefault(f.raw_id, f.path)
    return out


def _resolve_identity(
    ctx: _Context,
    row: AgentSession | Workflow,
    kind: DefinitionKind,
    existing: dict[str, dict[str, str]],
    plan: _Plan,
    label: str,
) -> tuple[str, str | None]:
    """The row's id (minted or repaired as needed) and its current file, if any."""
    other = "workflow" if kind == "agent" else "agent"
    ident = row.export_id
    reason: str | None = None
    if ident and not _ID_RE.match(ident):
        reason = f"its portable id '{ident}' isn't a valid file id"
    elif ident and (ident in existing[other] or ident in plan.ids_by_kind.get(other, set())):
        reason = f"its portable id '{ident}' is already used by a {other}"
    if reason:
        ctx.warn(None, f"{label}: {reason}; a new one was minted")
        ident = None
    if not ident:
        ident = str(uuid.uuid4())
        plan.link(row, {"export_id": ident})
    plan.ids_by_kind.setdefault(kind, set()).add(ident)
    return ident, existing[kind].get(ident)


async def export_definitions(
    session: AsyncSession, root: Path, *, overwrite: bool = False
) -> DefinitionsExportResult:
    """Write a file for every agent and workflow, linking each row to it."""
    dset = load_definitions(root)
    existing = _existing_ids(dset)
    plan = _Plan(taken_paths={p.lower() for p in dset.by_path})
    result = DefinitionsExportResult(root=str(root))
    roles = {r.id: r.name for r in (await session.execute(select(Role))).scalars()}
    ctx = _Context(root=root, roles=roles, result=result)
    pending: list[tuple[str, str]] = []

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
    for agent in agents:
        label = f"agent '{agent.title}'"
        ident, current = _resolve_identity(ctx, agent, "agent", existing, plan, label)
        adhoc = agent.topic_id is not None or agent.chat_id is not None
        path = current or plan.claim_path(
            root,
            "agents/adhoc" if adhoc else "agents",
            _short_slug(agent.title or "", 48) or "agent",
            AGENT_FILE_SUFFIX,
        )
        agent_paths[agent.id] = path
        entry = ExportedDefinition(
            kind="agent", path=path, id=ident, name=agent.title, source_id=agent.id
        )
        if current and not overwrite:
            result.skipped.append(entry)
            continue
        doc = _agent_document(ctx, agent, ident, path)
        plan.link(agent, _accepted(_report_invalid(ctx, "agent", path, doc)))
        pending.append((path, render_document(doc)))
        result.written.append(entry)

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
    for workflow in workflows:
        label = f"workflow '{workflow.name}'"
        ident, current = _resolve_identity(ctx, workflow, "workflow", existing, plan, label)
        path = current or plan.claim_path(
            root,
            "workflows",
            _short_slug(workflow.name or "", 48) or "workflow",
            WORKFLOW_FILE_SUFFIX,
        )
        entry = ExportedDefinition(
            kind="workflow", path=path, id=ident, name=workflow.name, source_id=workflow.id
        )
        if current and not overwrite:
            result.skipped.append(entry)
            continue
        doc = _workflow_document(ctx, workflow, ident, path, agent_paths)
        plan.link(workflow, _accepted(_report_invalid(ctx, "workflow", path, doc)))
        pending.append((path, render_document(doc)))
        result.written.append(entry)

    # Linking a row is bookkeeping, not an edit: keep ``updated_at`` as it was,
    # or the Workflows gallery (sorted by it) would reshuffle after an export.
    for model, row_id, values in plan.links.values():
        await session.execute(
            update(model)
            .where(model.id == row_id)
            .values(**values, updated_at=model.updated_at)
            .execution_options(synchronize_session=False)
        )
    # Ids are committed only once every file is on disk, so a failed write
    # never leaves a row pointing at a file that doesn't exist.
    for path, text in pending:
        _write_atomic(root / path, text)
    # Loading the rows above may have cached a scan from before these files.
    overlay.invalidate()
    await session.commit()
    return result
