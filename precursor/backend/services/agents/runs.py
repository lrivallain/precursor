"""Run persistence: opening an agent's executions, loading them, and writing
execution state to a run while mirroring it onto its agent.

``AgentManager`` delegates here; this module must not import ``manager`` at
runtime.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentRun,
    AgentSession,
)
from precursor.backend.services.definitions import overlay as definition_overlay

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# Execution state written by :meth:`AgentManager._patch_run`. ``_MIRRORED`` lands
# on the ``AgentRun`` *and* is copied onto its ``AgentSession`` (the denormalised
# view the Agents list, inbox, metrics, fleet governor and scheduler read).
# ``_RUN_ONLY`` has no column on the definition and stays on the run.
_MIRRORED_RUN_FIELDS = frozenset(
    {
        "status",
        "active_prompt",
        "blocked_question",
        "result_summary",
        "error",
        "step_count",
        "progress",
        "progress_label",
        "total_input_tokens",
        "total_output_tokens",
        "finished_at",
        "last_activity_at",
    }
)

_RUN_ONLY_FIELDS = frozenset(
    {
        "copilot_session_id",
        "started_at",
        "stall_count",
        "last_progress",
        "workflow_run_id",
        "workflow_run_step_id",
    }
)


class RunStore:
    def __init__(self, manager: AgentManager) -> None:
        # A back-reference read at call time (never captured bound methods), so a
        # test patching the manager still takes effect.
        self._manager = manager

    async def open_run(
        self,
        agent_id: int,
        *,
        trigger: str = "manual",
        workflow_run_id: int | None = None,
        workflow_run_step_id: int | None = None,
        inherit_session: bool = False,
    ) -> AgentRun | None:
        """Start a new execution of ``agent_id`` and make it the agent's current run.

        A run snapshots the capabilities it will execute under (model, MCP/skills/
        memory toggles, server scope, role, approval policy) so a later edit to the
        definition — or a concurrent workflow step applying its own overrides —
        can't change what an in-flight execution is running with.

        ``inherit_session`` carries the previous run's ``copilot_session_id`` over
        so the SDK resumes the same conversation (``clear_session(keep_id=True)``,
        scheduled ``/agent <uuid>`` nudges). Otherwise the run starts with no
        handle and mints one on its first connect.
        """
        m = self._manager
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_id)
            if agent is None:
                return None
            prior: AgentRun | None = None
            if agent.current_run_id is not None:
                prior = await session.get(AgentRun, agent.current_run_id)
            run = AgentRun(
                agent_id=agent.id,
                trigger=trigger,
                workflow_run_id=workflow_run_id,
                workflow_run_step_id=workflow_run_step_id,
                copilot_session_id=(
                    prior.copilot_session_id if inherit_session and prior is not None else None
                ),
                status="pending",
                model=agent.model,
                use_mcp=agent.use_mcp,
                use_skills=agent.use_skills,
                use_memory=agent.use_memory,
                mcp_servers=agent.mcp_servers,
                approval_policy=agent.approval_policy,
                role=agent.role,
                started_at=datetime.now(UTC),
            )
            if (source := definition_overlay.provenance(agent)) is not None:
                run.definition_path, run.definition_hash = source
            session.add(run)
            await session.flush()
            agent.current_run_id = run.id
            await session.commit()
            await session.refresh(run)
            m._agent_runs[agent.id] = run.id
            return run

    async def resolve_run(self, agent_id: int) -> AgentRun | None:
        """Return the agent's current run, opening one if it has never executed.

        Dispatch is agent-addressed everywhere the user can reach it (routers,
        slash commands, the scheduler), so "which execution?" has to be answered
        here. Agents migrated from before runs existed, and agents whose run was
        deleted, get a fresh one rather than failing the call.
        """
        m = self._manager
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_id)
            if agent is None:
                return None
            if agent.current_run_id is not None:
                run = await session.get(AgentRun, agent.current_run_id)
                if run is not None:
                    m._agent_runs[agent_id] = run.id
                    return run
        return await m._open_run(agent_id, trigger="manual")

    async def get_run(self, run_id: int) -> AgentRun | None:
        async with SessionLocal() as session:
            return await session.get(AgentRun, run_id)

    async def load_run(self, run_id: int) -> tuple[AgentRun, AgentSession] | None:
        """Load a run together with the definition it executes, or ``None``."""
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return None
            agent = await session.get(AgentSession, run.agent_id)
            if agent is None:
                return None
            return run, agent

    async def patch_run(self, run_id: int, **values: Any) -> None:
        """Write execution state to a run and mirror it onto its agent.

        The run is authoritative; ``AgentSession`` keeps a denormalised copy of
        the *current* run's state so the Agents list, inbox, metrics, fleet
        governor and scheduler keep reading a single row. Keys outside
        :data:`_MIRRORED_RUN_FIELDS` (``retry_count``, ``next_retry_at``, …) are
        definition-level and land on the agent only — this is the one place that
        split is made, so the mirror can't drift.

        The mirror is skipped when the run is no longer the agent's current one:
        a finished workflow step must not overwrite whatever is running now.
        """
        if not values:
            return
        run_only = {k: v for k, v in values.items() if k in _RUN_ONLY_FIELDS}
        mirrored = {k: v for k, v in values.items() if k in _MIRRORED_RUN_FIELDS}
        agent_only = {
            k: v
            for k, v in values.items()
            if k not in _RUN_ONLY_FIELDS and k not in _MIRRORED_RUN_FIELDS
        }
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            for key, value in {**run_only, **mirrored}.items():
                setattr(run, key, value)
            agent = await session.get(AgentSession, run.agent_id)
            if agent is not None:
                for key, value in agent_only.items():
                    setattr(agent, key, value)
                if agent.current_run_id == run.id:
                    for key, value in mirrored.items():
                        setattr(agent, key, value)
            await session.commit()

    async def load_agent(self, agent_id: int) -> AgentSession | None:
        async with SessionLocal() as session:
            return await session.get(AgentSession, agent_id)

    async def patch_agent(self, agent_id: int, **values: Any) -> None:
        if not values:
            return
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_id)
            if agent is None:
                return
            for key, value in values.items():
                setattr(agent, key, value)
            await session.commit()
