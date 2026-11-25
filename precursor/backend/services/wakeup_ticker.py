"""Background ticker that wakes paused conversations up (``services/wakeups``).

Each due wakeup sends its prompt back into its conversation: a headless turn
for a topic or a chat (``services/turn``), a follow-up message for an agent. A
conversation still busy (a turn generating, an agent running or waiting on an
approval) keeps its wakeup until the next tick, so a wakeup never cuts into a
running turn.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentSession, Wakeup
from precursor.backend.services import wakeups
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.events import (
    broadcast_events,
    is_streaming,
    publish_wakeup_changed,
)

logger = logging.getLogger(__name__)

# Agent states a wakeup waits out. ``blocked`` is not one: the agent asked a
# question and stopped, and its own wakeup is a fine answer.
_AGENT_BUSY = frozenset({"pending", "running", "needs_approval"})


class WakeupTicker(BackgroundPoll):
    task_name = "wakeup-ticker"
    label = "Wakeup ticker"
    poll_floor = 5

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        # Held so the turns a tick starts are not garbage-collected mid-run.
        self._turns: set[asyncio.Task[object]] = set()
        # A Wake now nudge can land during a periodic tick: one scan at a time,
        # so a due wakeup is consumed (and dispatched) exactly once.
        self._scan = asyncio.Lock()

    @property
    def poll_seconds(self) -> int:
        return self._settings.wakeup_poll_seconds

    async def run_once(self) -> None:
        async with self._scan:
            await self._scan_due()

    async def _scan_due(self) -> None:
        async with SessionLocal() as session:
            due = await wakeups.due(session)
            woken = []
            for wakeup in due:
                if await _busy(session, wakeup):
                    continue
                woken.append((wakeups.target_of(wakeup), wakeups.wake_message(wakeup)))
                await session.delete(wakeup)
            if not woken:
                return
            await session.commit()
        await publish_wakeup_changed()
        # The turns may start inside a Wake now request: untag them from its
        # window, or that window would drop their events as its own echoes.
        with broadcast_events():
            for (kind, target_id), message in woken:
                self._wake(kind, target_id, message)

    def _wake(self, kind: str, target_id: int, message: str) -> None:
        if kind == "agent":
            from precursor.backend.services.agents.manager import get_agent_manager

            manager = get_agent_manager()
            manager.enqueue(manager.send_message(target_id, message))
            return
        from precursor.backend.services.turn import run_container_turn

        task = asyncio.create_task(
            run_container_turn(
                kind,  # type: ignore[arg-type]
                target_id,
                message,
                message_kind=wakeups.MESSAGE_KIND_WAKEUP,
            ),
            name=f"wakeup-{kind}-{target_id}",
        )
        self._turns.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[object]) -> None:
        self._turns.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Wakeup turn %s failed", task.get_name(), exc_info=task.exception())


async def _busy(session: AsyncSession, wakeup: Wakeup) -> bool:
    kind, target_id = wakeups.target_of(wakeup)
    if kind != "agent":
        return is_streaming(kind, target_id)
    from precursor.backend.services.agents.manager import get_agent_manager

    if not get_agent_manager().ready:
        return True
    agent = await session.get(AgentSession, target_id)
    return agent is not None and agent.status in _AGENT_BUSY


get_wakeup_ticker = WakeupTicker.instance
