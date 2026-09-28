"""The integrity check as the running app sees it: folder + this instance's database.

On top of the folder checks, this knows which roles and MCP servers exist here
and which database rows are linked to a file (through ``export_id``), so it can
tell what the export still has to write.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AgentSession, Role, Workflow
from precursor.backend.schemas.definitions_api import (
    DefinitionIssue,
    DefinitionsCheckReport,
    DefinitionsDatabaseLinks,
    UnlinkedAgent,
    UnlinkedWorkflow,
)
from precursor.backend.services.definitions import overlay
from precursor.backend.services.definitions.checker import InstanceNames, build_report
from precursor.backend.services.definitions.loader import DefinitionSet, load_definitions
from precursor.backend.services.mcp.client import get_mcp_client_manager


async def instance_names(session: AsyncSession) -> InstanceNames:
    roles = frozenset((await session.execute(select(Role.name))).scalars())
    servers = frozenset(entry.name for entry in get_mcp_client_manager().list_entries())
    return InstanceNames(roles=roles, mcp_servers=servers)


async def database_links(
    session: AsyncSession, dset: DefinitionSet
) -> tuple[dict[str, frozenset[str]], DefinitionsDatabaseLinks]:
    file_ids: dict[str, set[str]] = {"agent": set(), "workflow": set()}
    for f in dset.files:
        if f.raw_id:
            file_ids[f.kind].add(f.raw_id)

    links = DefinitionsDatabaseLinks()
    agent_ids: set[str] = set()
    agents = await session.execute(
        select(
            AgentSession.id,
            AgentSession.public_id,
            AgentSession.title,
            AgentSession.export_id,
            AgentSession.archived_at,
        )
        .where(AgentSession.inline.is_(False))
        .order_by(AgentSession.id)
    )
    for row_id, public_id, title, export_id, archived_at in agents:
        if export_id:
            agent_ids.add(export_id)
        if export_id and export_id in file_ids["agent"]:
            links.linked_agents += 1
        else:
            links.unlinked_agents.append(
                UnlinkedAgent(
                    id=row_id, public_id=public_id, title=title, archived=archived_at is not None
                )
            )

    workflow_ids: set[str] = set()
    workflows = await session.execute(
        select(Workflow.id, Workflow.name, Workflow.export_id, Workflow.archived_at).order_by(
            Workflow.id
        )
    )
    for row_id, name, export_id, archived_at in workflows:
        if export_id:
            workflow_ids.add(export_id)
        if export_id and export_id in file_ids["workflow"]:
            links.linked_workflows += 1
        else:
            links.unlinked_workflows.append(
                UnlinkedWorkflow(id=row_id, name=name, archived=archived_at is not None)
            )
    return {"agent": frozenset(agent_ids), "workflow": frozenset(workflow_ids)}, links


async def check_folder(session: AsyncSession, root: Path) -> DefinitionsCheckReport:
    dset = await asyncio.to_thread(load_definitions, root)
    names = await instance_names(session)
    linked, links = await database_links(session, dset)
    report = build_report(dset, names, linked_ids=linked, database=links)

    missing_agents, missing_workflows = len(links.unlinked_agents), len(links.unlinked_workflows)
    if missing_agents or missing_workflows:
        parts = []
        if missing_agents:
            parts.append(f"{missing_agents} agent{'s' if missing_agents > 1 else ''}")
        if missing_workflows:
            parts.append(f"{missing_workflows} workflow{'s' if missing_workflows > 1 else ''}")
        if overlay.finalized() is not None:
            # Nothing else declares them any more: they can't run until restored.
            report.issues.append(
                DefinitionIssue(
                    severity="error",
                    message=f"{' and '.join(parts)} have lost their definition file; restore "
                    "them from git or the database copy taken at cleanup",
                )
            )
            report.error_count += 1
            report.ok = False
        else:
            report.issues.append(
                DefinitionIssue(
                    severity="warning",
                    message=f"{' and '.join(parts)} in the database have no definition file "
                    "yet; the export writes them",
                )
            )
            report.warning_count += 1
    return report
