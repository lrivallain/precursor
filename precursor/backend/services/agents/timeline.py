"""The durable per-agent event timeline behind an agent's transcript.

Hydrates the in-memory cache from the ``agent_events`` archive, persists new
events, and serves the transcript (whole or paged) with any parked permission
cards appended. The caches themselves (``_events``/``_loaded``) stay on
``AgentManager``; this module must not import ``manager`` at runtime.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentEventRecord
from precursor.backend.schemas.agent import AgentEvent, AgentEventPage
from precursor.backend.services.agents.event_normalizer import normalize_event

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)


class Timeline:
    def __init__(self, manager: AgentManager) -> None:
        # Held as a back-reference and read at call time, never captured as
        # bound methods, so a test patching the manager still takes effect.
        self._manager = manager

    async def timeline(
        self, agent_id: int, *, agent_run_id: int | None = None
    ) -> tuple[list[AgentEvent], list[AgentEvent]]:
        """Split the transcript into its stable prefix and its volatile tail.

        The archived history is append-only, which is what lets a live reader ask
        for only what it hasn't seen (:meth:`get_events_page`). Unresolved
        permission cards are *not* archived — they appear and vanish as approvals
        are answered — so they are returned separately instead of being counted
        into a cursor they would immediately invalidate.

        The archive is per *agent* — the transcript the user reads spans every
        execution — but the live fallback and the pending-approval cards belong
        to the agent's **current** run, the only execution they can act on.

        Pass ``agent_run_id`` to read a single execution. Two workflows driving
        one reusable agent at the same time otherwise interleave into a single
        unreadable conversation, each answering the other's prompt (issue #242).
        """
        await self.ensure_loaded(agent_id)
        # Which execution this read is about: the requested one, else the
        # agent's current run (the only one an approval card could act on).
        run = (
            await self._manager._run(agent_run_id)
            if agent_run_id is not None
            else await self._manager._resolve_run(agent_id)
        )
        live = self._manager._live.get(run.id) if run is not None else None
        events = [
            ev
            for ev in self._manager._events.get(agent_id, [])
            if agent_run_id is None or ev.agent_run_id == agent_run_id
        ]
        if not events:
            # Nothing archived (neither in memory nor the DB) — e.g. a session
            # resumed after a restart that hasn't re-emitted yet. Fall back to
            # whatever the live session can replay.
            if live is None:
                loaded = await self._manager._load_run(run.id) if run is not None else None
                if loaded is None or not loaded[0].copilot_session_id:
                    return [], []
                live = await self._manager._ensure_live(loaded[1], loaded[0])
            try:
                raw = await live.sdk_session.get_events()
            except Exception:
                logger.debug("get_events failed for agent %s", agent_id, exc_info=True)
                raw = []
            events = [normalize_event(ev) for ev in raw or []]
        # Unresolved permission requests render as inline workflow steps so the
        # approval card appears in-place (with details of what's requested)
        # rather than floating detached from the timeline.
        pending = (
            [
                AgentEvent(
                    kind="permission_request",
                    text=info.get("title"),
                    request_id=info.get("request_id"),
                    data=info,
                )
                for info in live.pending_info.values()
            ]
            if live is not None
            else []
        )
        return events, pending

    async def get_events(
        self, agent_id: int, *, agent_run_id: int | None = None
    ) -> list[AgentEvent]:
        """The whole transcript: stable history followed by any parked approvals.

        See :meth:`timeline` for how ``agent_run_id`` scopes the read.
        """
        stable, pending = await self.timeline(agent_id, agent_run_id=agent_run_id)
        return [*stable, *pending]

    async def get_events_page(
        self, agent_id: int, *, agent_run_id: int | None = None, after: int = 0
    ) -> AgentEventPage:
        """The transcript from ``after`` onward, for an incremental live reader.

        A cursor past the end no longer addresses this transcript — it was
        cleared, pruned by retention, or was taken against a different run — so
        answer with the whole thing and flag it as a replacement rather than
        silently skipping the events the caller is missing.
        """
        stable, pending = await self.timeline(agent_id, agent_run_id=agent_run_id)
        reset = after < 0 or after > len(stable)
        return AgentEventPage(
            events=stable[0 if reset else after :],
            pending=pending,
            cursor=len(stable),
            reset=reset,
        )

    async def ensure_loaded(self, agent_id: int) -> None:
        """Hydrate the in-memory timeline from the ``agent_events`` archive once.

        After a process restart the live cache is empty and the SDK only replays
        ``SessionStartData`` on resume, so the durable history lives only in the
        DB. Load it lazily the first time an agent is touched (an event arriving
        or a timeline read) and mark it loaded so we don't re-read per event.
        """
        if agent_id in self._manager._loaded:
            return
        async with self._manager._events_lock:
            if agent_id in self._manager._loaded:
                return
            async with SessionLocal() as session:
                rows = (
                    await session.execute(
                        select(AgentEventRecord.payload, AgentEventRecord.agent_run_id)
                        .where(AgentEventRecord.agent_session_id == agent_id)
                        .order_by(AgentEventRecord.id)
                    )
                ).all()
            archived: list[AgentEvent] = []
            for payload, run_id in rows:
                try:
                    parsed = AgentEvent.model_validate_json(payload)
                    # The column is the authority: rows archived before the event
                    # payload carried a run id still resolve to their execution.
                    if run_id is not None:
                        parsed.agent_run_id = run_id
                    archived.append(parsed)
                except Exception:
                    logger.debug(
                        "skipping malformed archived event for agent %s", agent_id, exc_info=True
                    )
            if archived:
                self._manager._events[agent_id] = archived
            self._manager._loaded.add(agent_id)


async def archive_event(
    agent_id: int, event: AgentEvent, *, agent_run_id: int | None = None
) -> None:
    """Persist one normalised event to the durable timeline archive."""
    try:
        async with SessionLocal() as session:
            session.add(
                AgentEventRecord(
                    agent_session_id=agent_id,
                    agent_run_id=agent_run_id,
                    payload=event.model_dump_json(),
                )
            )
            await session.commit()
    except Exception:
        logger.debug("failed to archive event for agent %s", agent_id, exc_info=True)
