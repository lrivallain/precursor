"""The agent watchdog: un-sticks runs left mid-turn by a crash or a wedged turn,
restarts a dead CLI server and releases live sessions idle past their TTL.

``AgentManager`` delegates here; this module must not import ``manager`` at
runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentRun,
    AgentSession,
)
from precursor.backend.services.app_settings import resolve_agents_watchdog_timeout
from precursor.backend.services.events import (
    publish_agent_changed,
)

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)

# How often the watchdog sweeps for stalled running sessions.
_WATCHDOG_INTERVAL_SECONDS = 60.0

# Every live SDK session keeps its conversation and tool-server wiring in the one
# shared CLI process until it is disconnected. Workflows open a fresh run per step
# and nothing else releases a finished one, so without this TTL they pile up until
# the CLI exhausts its heap and dies. A released session resumes from its on-disk
# state (``copilot_session_id``) on its next turn.
_IDLE_SESSION_TTL_SECONDS = 15 * 60


class Watchdog:
    def __init__(self, manager: AgentManager) -> None:
        # A back-reference read at call time (never captured bound methods), so a
        # test patching the manager still takes effect.
        self._manager = manager

    async def mark_interrupted_on_boot(self) -> None:
        """Flag executions that were mid-turn when the process last died.

        The runs are the authoritative rows and must be flipped too: they are
        what the fleet governor counts, and nothing else ever revisits them, so a
        run left ``running`` by a crash would hold one of the
        ``agents_max_concurrent`` slots for the lifetime of the install.
        """
        async with SessionLocal() as session:
            from sqlalchemy import update

            await session.execute(
                update(AgentRun).where(AgentRun.status == "running").values(status="interrupted")
            )
            await session.execute(
                update(AgentSession)
                .where(AgentSession.status == "running")
                .values(status="interrupted")
            )
            await session.commit()

    async def loop(self) -> None:
        """Periodically interrupt running sessions that have gone silent.

        A turn can wedge (a hung tool, a dropped runtime connection) and leave a
        session pinned in ``running`` forever, never notifying back. This sweep
        flips such sessions to ``interrupted`` (resumable) with a reason, so they
        surface in the UI and the user can Resume to retry the in-flight prompt.
        The same tick restarts a CLI server that died and releases live sessions
        that have sat idle past ``_IDLE_SESSION_TTL_SECONDS``.
        """
        m = self._manager
        sweeps = (m._ensure_runtime, m._watchdog_sweep, m._release_idle_sessions)
        while m._ready:
            try:
                await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
                for sweep in sweeps:
                    try:
                        await sweep()
                    except Exception:
                        logger.debug("agent watchdog %s failed", sweep.__name__, exc_info=True)
            except asyncio.CancelledError:
                break

    async def release_idle_sessions(self) -> None:
        """Disconnect live sessions whose run has been at rest past the TTL.

        A run with no resume handle is kept while it is still the agent's current
        run: releasing it would start the agent's next turn on an empty
        conversation. Once superseded, nothing will ever send to it again.
        """
        m = self._manager
        if not m._live:
            return
        cutoff = datetime.now(UTC) - timedelta(seconds=_IDLE_SESSION_TTL_SECONDS)
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(AgentRun, AgentSession.current_run_id)
                    .join(AgentSession, AgentSession.id == AgentRun.agent_id)
                    .where(AgentRun.id.in_(list(m._live)))
                )
            ).all()
        for run, current_run_id in rows:
            if run.status in {"running", "needs_approval", "pending"}:
                continue
            if not run.copilot_session_id and run.id == current_run_id:
                continue
            ref = run.last_activity_at or run.finished_at or run.updated_at or run.created_at
            if ref is None:
                continue
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=UTC)
            live = m._live.get(run.id)
            # No ``await`` between this check and ``_teardown_run`` popping the
            # session, so one ``_ensure_live`` has just handed out is never torn
            # down under its caller.
            if (
                ref < cutoff
                and live is not None
                and time.monotonic() - live.last_used > _IDLE_SESSION_TTL_SECONDS
            ):
                logger.debug("agent %s run %s: releasing idle live session", run.agent_id, run.id)
                await m._teardown_run(run.id)

    async def sweep(self) -> None:
        """Interrupt every *run* that has gone silent, not every agent.

        The sweep is run-scoped for two reasons. ``AgentSession.status`` and
        ``last_activity_at`` only mirror the agent's *current* run, so a wedged
        run that some other execution has since superseded would be invisible
        here and stay pinned in ``running`` forever — leaking a concurrency slot
        from the fleet governor, which counts runs. And the teardown has to hit
        only the wedged run: an agent shared by two workflows may have a second,
        perfectly healthy session mid-turn that must survive the sweep.
        """
        m = self._manager
        async with SessionLocal() as session:
            timeout = await resolve_agents_watchdog_timeout(session)
            cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
            rows = (
                await session.execute(
                    select(AgentRun, AgentSession.topic_id, AgentSession.chat_id)
                    .join(AgentSession, AgentSession.id == AgentRun.agent_id)
                    .where(AgentRun.status == "running")
                )
            ).all()
            stale: list[tuple[int, int, int | None, int | None]] = []
            for run, topic_id, chat_id in rows:
                ref = run.last_activity_at or run.started_at or run.updated_at or run.created_at
                if ref is None:
                    continue
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=UTC)
                if ref < cutoff:
                    stale.append((run.id, run.agent_id, topic_id, chat_id))
        reason = (
            f"No runtime activity for over {max(1, timeout // 60)} min — "
            "interrupted by the watchdog. Resume to retry."
        )
        # Drop the wedged live session so a Resume rebuilds it clean, then signal
        # the UI. Done outside the read transaction to keep it tight, and through
        # ``_patch_run`` so the agent-level mirror is maintained in one place.
        for run_id, agent_id, topic_id, chat_id in stale:
            logger.warning(
                "agent %s run %s: interrupted by watchdog (idle > %ss)", agent_id, run_id, timeout
            )
            await m._patch_run(run_id, status="interrupted", error=reason)
            with contextlib.suppress(Exception):
                await m._teardown_run(run_id)
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=topic_id,
                chat_id=chat_id,
                agent_run_id=run_id,
            )
