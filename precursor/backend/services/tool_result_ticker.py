"""Background ticker that prunes expired TOOL-result content.

A single lightweight task periodically replaces the ``content`` of aged TOOL
rows with a short placeholder (see ``services/tool_result_retention``), bounding
long-term DB growth. Gated by the same ``scheduler_enabled`` flag as the other
tickers; the poll interval defaults to daily. When retention is disabled the
sweep is a cheap no-op, so the ticker can keep running regardless.
"""

from __future__ import annotations

from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.tool_result_retention import prune_expired_tool_results


class ToolResultTicker(BackgroundPoll):
    task_name = "tool-result-ticker"
    label = "Tool-result retention ticker"
    poll_floor = 60

    @property
    def poll_seconds(self) -> int:
        return self._settings.tool_result_retention_poll_seconds

    async def run_once(self) -> None:
        await prune_expired_tool_results()


_ticker: ToolResultTicker | None = None


def get_tool_result_ticker() -> ToolResultTicker:
    global _ticker
    if _ticker is None:
        _ticker = ToolResultTicker()
    return _ticker
