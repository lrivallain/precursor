"""Shared clean-up for the definition-file tests.

The suite shares one database, and some modules assume they run early (no
agents, Agents mode off). These tests create agents and workflows and switch
Agents mode on, so they hand the database back the way they found it.
"""

from __future__ import annotations

from dataclasses import dataclass

# App settings these tests change, restored afterwards.
_SETTINGS = (
    "agents_enabled",
    "definitions_source",
    "definitions_migrated",
    "definitions_finalized",
)


@dataclass
class DatabaseMark:
    settings: dict[str, str | None]
    max_agent_id: int
    max_workflow_id: int


async def mark_database() -> DatabaseMark:
    from sqlalchemy import func, select

    from precursor.backend.db import SessionLocal, init_db
    from precursor.backend.models import AgentSession, AppSetting, Workflow

    await init_db()
    async with SessionLocal() as session:
        values: dict[str, str | None] = {}
        for key in _SETTINGS:
            row = await session.get(AppSetting, key)
            values[key] = row.value if row else None
        max_agent = (await session.execute(select(func.max(AgentSession.id)))).scalar() or 0
        max_workflow = (await session.execute(select(func.max(Workflow.id)))).scalar() or 0
        return DatabaseMark(values, max_agent, max_workflow)


async def restore_database(mark: DatabaseMark) -> None:
    """Drop every agent and workflow created since ``mark``, with their runs."""
    from sqlalchemy import delete, select

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import (
        AgentRun,
        AgentSession,
        AppSetting,
        Workflow,
        WorkflowRun,
        WorkflowRunStep,
        WorkflowStep,
    )

    async with SessionLocal() as session:
        new_runs = select(WorkflowRun.id).where(WorkflowRun.workflow_id > mark.max_workflow_id)
        await session.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id.in_(new_runs)))
        await session.execute(
            delete(WorkflowRun).where(WorkflowRun.workflow_id > mark.max_workflow_id)
        )
        await session.execute(
            delete(WorkflowStep).where(
                (WorkflowStep.workflow_id > mark.max_workflow_id)
                | (WorkflowStep.agent_id > mark.max_agent_id)
            )
        )
        await session.execute(delete(Workflow).where(Workflow.id > mark.max_workflow_id))
        # Clear the agent → run pointer first: the two tables reference each other.
        await session.execute(
            AgentSession.__table__.update()
            .where(AgentSession.id > mark.max_agent_id)
            .values(current_run_id=None)
        )
        await session.execute(delete(AgentRun).where(AgentRun.agent_id > mark.max_agent_id))
        await session.execute(delete(AgentSession).where(AgentSession.id > mark.max_agent_id))
        for key, value in mark.settings.items():
            row = await session.get(AppSetting, key)
            if value is None:
                if row is not None:
                    await session.delete(row)
            elif row is None:
                session.add(AppSetting(key=key, value=value))
            else:
                row.value = value
        await session.commit()
        from precursor.backend.services.definitions import overlay

        await overlay.refresh_source(session)


async def wipe_agents_and_workflows() -> None:
    """Start from an install with no agents or workflows at all.

    The migration looks at every row, and the shared database holds other
    modules' leftovers (some mid-run), so its tests start from a clean slate.
    """
    from sqlalchemy import delete

    from precursor.backend.db import SessionLocal
    from precursor.backend.models import (
        AgentRun,
        AgentSession,
        Workflow,
        WorkflowRun,
        WorkflowRunStep,
        WorkflowStep,
    )

    async with SessionLocal() as session:
        await session.execute(delete(WorkflowRunStep))
        await session.execute(delete(WorkflowRun))
        await session.execute(delete(WorkflowStep))
        await session.execute(delete(Workflow))
        await session.execute(AgentSession.__table__.update().values(current_run_id=None))
        await session.execute(delete(AgentRun))
        await session.execute(delete(AgentSession))
        await session.commit()
