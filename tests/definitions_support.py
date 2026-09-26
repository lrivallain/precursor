"""Shared clean-up for the definition-file tests.

The suite shares one database, and some modules assume they run early (no
agents, Agents mode off). These tests create agents and workflows and switch
Agents mode on, so they hand the database back the way they found it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DatabaseMark:
    agents_enabled: str | None
    max_agent_id: int
    max_workflow_id: int


async def mark_database() -> DatabaseMark:
    from sqlalchemy import func, select

    from precursor.backend.db import SessionLocal, init_db
    from precursor.backend.models import AgentSession, AppSetting, Workflow

    await init_db()
    async with SessionLocal() as session:
        setting = await session.get(AppSetting, "agents_enabled")
        max_agent = (await session.execute(select(func.max(AgentSession.id)))).scalar() or 0
        max_workflow = (await session.execute(select(func.max(Workflow.id)))).scalar() or 0
        return DatabaseMark(setting.value if setting else None, max_agent, max_workflow)


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
        setting = await session.get(AppSetting, "agents_enabled")
        if mark.agents_enabled is None:
            if setting is not None:
                await session.delete(setting)
        elif setting is not None:
            setting.value = mark.agents_enabled
        await session.commit()
