"""Folder-wide integrity check for agent and workflow definition files.

Per-file problems come from the loader; this adds what only the whole folder
(and, when available, this instance's database) can tell: duplicate ids, agent
paths that point nowhere, and names that don't exist here.

Severity follows one rule: **error** when Precursor can't use a file or a
workflow can't run as written; **warning** when it runs but probably not as
intended. An unknown role or MCP server is a warning, because definitions are
portable — the same file may be valid on another machine.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from precursor.backend.schemas.definitions_api import (
    DefinitionFileSummary,
    DefinitionIssue,
    DefinitionsCheckReport,
    DefinitionsDatabaseLinks,
)
from precursor.backend.services.definitions.loader import DefinitionSet


@dataclass(frozen=True)
class InstanceNames:
    """What exists on this instance, for the name checks. ``None`` skips a check."""

    roles: frozenset[str] | None = None
    mcp_servers: frozenset[str] | None = None


def _duplicate_ids(dset: DefinitionSet) -> list[DefinitionIssue]:
    by_id: dict[str, list[str]] = defaultdict(list)
    for f in dset.files:
        if f.raw_id:
            by_id[f.raw_id].append(f.path)
    issues: list[DefinitionIssue] = []
    for ident, paths in sorted(by_id.items()):
        if len(paths) < 2:
            continue
        for path in paths:
            others = ", ".join(p for p in paths if p != path)
            issues.append(
                DefinitionIssue(
                    severity="error",
                    path=path,
                    location="id",
                    message=f"id '{ident}' is also used by {others}; every file needs its own id",
                )
            )
    return issues


def _agent_references(dset: DefinitionSet) -> list[DefinitionIssue]:
    agent_paths_by_name: dict[str, list[str]] = defaultdict(list)
    for f in dset.files:
        if f.kind == "agent":
            agent_paths_by_name[PurePosixPath(f.path).name].append(f.path)

    issues: list[DefinitionIssue] = []
    for wf_file, workflow in dset.workflows():
        for step in workflow.steps:
            if step.agent is None:
                continue
            location = f"steps[{step.key}].agent"
            target = dset.by_path.get(step.agent)
            if target is None:
                message = f"no agent file at {step.agent}"
                candidates = agent_paths_by_name.get(PurePosixPath(step.agent).name, [])
                if candidates:
                    message += f" (did you mean {' or '.join(candidates)}?)"
                message += "; paths are relative to the definitions folder"
                issues.append(
                    DefinitionIssue(
                        severity="error", path=wf_file.path, location=location, message=message
                    )
                )
            elif target.definition is None:
                issues.append(
                    DefinitionIssue(
                        severity="error",
                        path=wf_file.path,
                        location=location,
                        message=f"{step.agent} has errors, so this step can't run",
                    )
                )
    return issues


def _role_names(dset: DefinitionSet, known: frozenset[str]) -> list[DefinitionIssue]:
    lowered = {name.lower() for name in known}
    issues: list[DefinitionIssue] = []
    for f in dset.files:
        role = getattr(f.definition, "role", None)
        if role is not None and role.lower() not in lowered:
            issues.append(
                DefinitionIssue(
                    severity="warning",
                    path=f.path,
                    location="role",
                    message=f"no role named '{role}' on this instance; the default persona "
                    "will be used",
                )
            )
    return issues


def _mcp_names(dset: DefinitionSet, known: frozenset[str]) -> list[DefinitionIssue]:
    def unknown(servers: list[str] | None) -> list[str]:
        return [s for s in servers or [] if s not in known]

    def warn(path: str, location: str, names: list[str]) -> DefinitionIssue:
        listed = ", ".join(f"'{n}'" for n in names)
        return DefinitionIssue(
            severity="warning",
            path=path,
            location=location,
            message=f"MCP server {listed} isn't configured on this instance; it will match nothing",
        )

    issues: list[DefinitionIssue] = []
    for f, agent in dset.agents():
        if missing := unknown(agent.capabilities.mcp_servers):
            issues.append(warn(f.path, "capabilities.mcp_servers", missing))
    for f, workflow in dset.workflows():
        for step in workflow.steps:
            if missing := unknown(step.capabilities.mcp_servers):
                issues.append(warn(f.path, f"steps[{step.key}].capabilities.mcp_servers", missing))
    return issues


def check_definitions(
    dset: DefinitionSet, names: InstanceNames | None = None
) -> list[DefinitionIssue]:
    """Every issue in the folder: per-file first, then cross-file, by path."""
    names = names or InstanceNames()
    issues = [issue for f in dset.files for issue in f.issues]
    issues += _duplicate_ids(dset)
    issues += _agent_references(dset)
    if names.roles is not None:
        issues += _role_names(dset, names.roles)
    if names.mcp_servers is not None:
        issues += _mcp_names(dset, names.mcp_servers)
    order = {f.path: i for i, f in enumerate(dset.files)}
    issues.sort(key=lambda i: (order.get(i.path or "", -1), i.severity != "error"))
    return issues


def build_report(
    dset: DefinitionSet,
    names: InstanceNames | None = None,
    *,
    linked_ids: dict[str, frozenset[str]] | None = None,
    database: DefinitionsDatabaseLinks | None = None,
) -> DefinitionsCheckReport:
    """``linked_ids`` maps a kind to the ids its database rows carry."""
    issues = check_definitions(dset, names)
    errors = sum(1 for i in issues if i.severity == "error")
    files = [
        DefinitionFileSummary(
            path=f.path,
            kind=f.kind,
            id=f.raw_id,
            name=f.name,
            valid=f.definition is not None,
            content_hash=f.content_hash,
            linked=None
            if linked_ids is None
            else (f.raw_id is not None and f.raw_id in linked_ids.get(f.kind, frozenset())),
        )
        for f in dset.files
    ]
    return DefinitionsCheckReport(
        root=str(dset.root),
        exists=dset.exists,
        ok=errors == 0,
        error_count=errors,
        warning_count=len(issues) - errors,
        files=files,
        issues=issues,
        database=database,
    )
