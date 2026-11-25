"""Background ticker that wakes paused conversations up (``services/wakeups``).

Each due wakeup sends its prompt back into its conversation: a headless turn
for a topic or a chat (``services/turn``), a follow-up message for an agent. A
conversation still busy (a turn generating, an agent running or waiting on an
approval) keeps its wakeup until the next tick, so a wakeup never cuts into a
running turn.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import Settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentRun, AgentSession, Wakeup
from precursor.backend.services import turn_lifecycle, wakeups
from precursor.backend.services.background_poll import BackgroundPoll
from precursor.backend.services.events import (
    broadcast_events,
    is_streaming,
    publish_wakeup_changed,
)

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
            woken, consumed = [], False
            for wakeup in due:
                verdict = await _verdict(session, wakeup)
                if verdict == "wait":
                    continue
                if verdict == "wake":
                    woken.append((wakeups.target_of(wakeup), wakeups.wake_message(wakeup)))
                await session.delete(wakeup)
                consumed = True
            if not consumed:
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
        # Registered as the conversation's running turn, so Stop and clear-history
        # cancel it like any other; also kept here for shutdown.
        turn_lifecycle.track(kind, target_id, task)  # type: ignore[arg-type]
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    async def stop(self) -> None:
        """Stop polling, then cancel the turns still running: the MCP clients and
        agent services they use are closed right after."""
        await super().stop()
        turns = [t for t in self._turns if not t.done()]
        for task in turns:
            task.cancel()
        if turns:
            await asyncio.wait(turns, timeout=10)


async def _verdict(session: AsyncSession, wakeup: Wakeup) -> Literal["wake", "wait", "drop"]:
    """Wake the conversation now, wait for it to be free, or drop a stale wakeup."""
    kind, target_id = wakeups.target_of(wakeup)
    if kind != "agent":
        return "wait" if is_streaming(kind, target_id) else "wake"
    from precursor.backend.services.agents.manager import get_agent_manager

    if not get_agent_manager().ready:
        return "wait"
    agent = await session.get(AgentSession, target_id)
    run = await session.get(AgentRun, wakeup.agent_run_id) if wakeup.agent_run_id else None
    if agent is None or run is None or agent.current_run_id != run.id:
        # Another execution took the agent over: the prompt belongs to the one
        # that paused, not to a history it never saw.
        return "drop"
    return "wait" if run.status in _AGENT_BUSY else "wake"


get_wakeup_ticker = WakeupTicker.instance
