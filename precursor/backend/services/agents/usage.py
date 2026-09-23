"""Token metering and the token-budget governor for agent runs.

Records each metered LLM round into the shared usage ledger and the run's
running totals, and parks a run as ``blocked`` once its agent's cumulative spend
crosses its budget. ``AgentManager`` delegates here; this module must not import
``manager`` at runtime.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentRun
from precursor.backend.services.events import publish_agent_changed
from precursor.backend.services.usage_stats import record_usage

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)


class UsageMeter:
    def __init__(self, manager: AgentManager) -> None:
        # Held as a back-reference and read at call time, never captured as
        # bound methods, so a test patching the manager still takes effect.
        self._manager = manager

    async def record(self, run_id: int, data: Any) -> None:
        """Meter an ``AssistantUsageData`` round into the shared usage ledger.

        Each agent LLM call lands as one ``source="agent"`` row tagged with the
        agent's linked container, so agent spend shows up in the global usage
        stats alongside chat/topic turns. ``SessionUsageInfoData`` is *not*
        recorded — it reports context-window occupancy, not billable deltas, so
        counting it would double-charge the turn.
        """
        prompt_tokens = int(getattr(data, "input_tokens", None) or 0)
        completion_tokens = int(getattr(data, "output_tokens", None) or 0)
        if not prompt_tokens and not completion_tokens:
            return
        model = getattr(data, "model", None)
        loaded = await self._manager._load_run(run_id)
        if loaded is None:
            return
        run, agent = loaded
        try:
            async with SessionLocal() as session:
                await record_usage(
                    session,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    source="agent",
                    model=str(model) if model else (run.model or agent.model),
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                )
                await session.commit()
        except Exception:
            logger.debug("failed to record agent usage for run %s", run_id, exc_info=True)

        # Accumulate the running totals on the run (drives the workflow step's
        # token delta and aggregate observability; mirrored onto the agent row for
        # the list view). Kept in a separate write so a usage-ledger failure above
        # doesn't lose the meter, and vice versa.
        await self._manager._patch_run(
            run_id,
            total_input_tokens=run.total_input_tokens + prompt_tokens,
            total_output_tokens=run.total_output_tokens + completion_tokens,
        )
        await self.enforce_budget(run_id)

    async def enforce_budget(self, run_id: int) -> None:
        """Park an agent as ``blocked`` once it burns through its token budget.

        The governor is a *soft* cap checked after each metered round: an
        in-flight turn finishes, but the next autonomous step won't start. Null
        budget = ungoverned. Already-terminal/blocked runs are left alone so we
        don't clobber a completion that landed in the same turn.

        The budget itself is cumulative governance and lives on the **definition**
        (spend across every execution counts against it), while the status change
        it triggers lands on the run that tripped it.
        """
        loaded = await self._manager._load_run(run_id)
        if loaded is None:
            return
        run, agent = loaded
        if agent.token_budget is None:
            return
        spent = await self._manager._agent_spend(agent.id)
        if spent < agent.token_budget:
            return
        if run.status not in ("running", "needs_approval"):
            return
        await self._manager._patch_run(
            run_id,
            status="blocked",
            active_prompt=None,
            blocked_question=(
                f"I've reached my token budget ({agent.token_budget:,} tokens; "
                f"{spent:,} spent). Review my progress and raise the budget or "
                "adjust the objective to continue."
            ),
        )
        await publish_agent_changed(
            agent_session_id=agent.id,
            topic_id=agent.topic_id,
            chat_id=agent.chat_id,
            agent_run_id=run_id,
        )


async def agent_spend(agent_id: int) -> int:
    """Total tokens this agent has ever burned, across every execution.

    Summed from the runs rather than read off ``AgentSession.total_*``: those
    columns are a write-through mirror of the agent's *current* run, so they
    reset whenever a new one opens and flip between concurrent drivers. The
    budget is cumulative governance, so it needs the real total — which the
    migration preserved by backfilling pre-split spend onto a synthetic run.
    """
    async with SessionLocal() as session:
        total = await session.execute(
            select(
                func.coalesce(
                    func.sum(AgentRun.total_input_tokens + AgentRun.total_output_tokens), 0
                )
            ).where(AgentRun.agent_id == agent_id)
        )
        return int(total.scalar() or 0)
