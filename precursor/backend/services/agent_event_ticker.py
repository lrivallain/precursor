"""Background ticker that prunes the archived agent timeline and workflow step inputs.

A single lightweight task periodically applies the ``agent_events`` retention
levers (see ``services/agent_event_retention``), bounding what is otherwise the
fastest-growing table in a busy install. It also clears old workflow step input
context (``services/workflow_step_retention``) on the same cadence. Gated by the same ``scheduler_enabled``
flag as the other tickers; the poll interval defaults to daily. When both levers
are disabled the sweep is a cheap no-op, so the ticker can keep running.
"""

from __future__ import annotations

from precursor.backend.services.agent_event_retention import prune_agent_events
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.workflow_step_retention import prune_expired_workflow_step_inputs


class AgentEventTicker(BackgroundPoll):
    task_name = "agent-event-ticker"
    label = "Agent-event retention ticker"
    poll_floor = 60

    @property
    def poll_seconds(self) -> int:
        return self._settings.agent_event_retention_poll_seconds

    async def run_once(self) -> None:
        await prune_agent_events()
        await prune_expired_workflow_step_inputs()


get_agent_event_ticker = AgentEventTicker.instance
