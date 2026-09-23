"""The run-scoped artifact blackboard (``agent_artifacts``).

Persists the outputs an agent publishes and clears a run's blackboard ahead of a
fresh objective. ``AgentManager`` delegates here; this module must not import
``manager`` at runtime.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, or_, select

from precursor.backend.db import SessionLocal

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)


class ArtifactStore:
    def __init__(self, manager: AgentManager) -> None:
        # Held as a back-reference and read at call time, never captured as
        # bound methods, so a test patching the manager still takes effect.
        self._manager = manager

    async def persist(self, run_id: int, artifacts: list[dict[str, str]], *, kind: str) -> None:
        """Write published outputs to the shared blackboard (``agent_artifacts``).

        ``kind`` here is the *provenance* — ``"result"`` for the auto-captured
        completion summary, ``"output"`` for a model-emitted ``ARTIFACT:`` line —
        stored on the row's ``key`` so downstream injection and the UI can tell
        them apart. The stored ``kind`` column is a rendering hint (kept as plain
        ``text``). De-duplicates an identical ``result`` so a completion that
        reposts the same summary doesn't stack duplicates. Best-effort: a
        blackboard write must never break the turn.

        Artifacts are scoped to the **run** that published them (``agent_id`` is
        kept alongside for agent-wide queries), so two workflows driving the same
        agent keep separate blackboards.
        """
        from precursor.backend.models.agent_artifact import AgentArtifact

        loaded = await self._manager._load_run(run_id)
        if loaded is None:
            return
        _run, agent = loaded
        try:
            async with SessionLocal() as session:
                for art in artifacts:
                    title = (art.get("title") or "Untitled").strip()[:200]
                    content = (art.get("content") or "").strip()[:100000]
                    if not content:
                        continue
                    if kind == "result":
                        existing = await session.execute(
                            select(AgentArtifact.id).where(
                                AgentArtifact.agent_run_id == run_id,
                                AgentArtifact.key == "result",
                                AgentArtifact.content == content,
                            )
                        )
                        if existing.first() is not None:
                            continue
                    session.add(
                        AgentArtifact(
                            agent_id=agent.id,
                            agent_run_id=run_id,
                            key=kind,
                            kind="text",
                            title=title,
                            content=content,
                        )
                    )
                await session.commit()
        except Exception:
            logger.debug("failed to persist artifacts for run %s", run_id, exc_info=True)


async def clear_artifacts(run_id: int) -> None:
    """Wipe a run's published artifacts ahead of a fresh objective.

    A re-run (manual restart, retry, edited task, a webhook re-trigger, or an
    upstream re-driving an already-completed dependent) should start with a
    clean blackboard so the new turn's outputs replace the previous run's
    rather than accumulating. Best-effort and idempotent — a no-op on first
    run. Deliberately *not* called from :meth:`send_message`: a conversational
    follow-up keeps the existing artifacts.

    Scoped to the run: a sibling execution of the same agent keeps its own
    blackboard intact. Rows with no run attribution at all — published
    straight through the API, or predating the split — belong to the agent
    rather than to any one execution, so they go too; leaving them would
    make them permanently unclearable.
    """
    from precursor.backend.models.agent_artifact import AgentArtifact
    from precursor.backend.models.agent_run import AgentRun

    try:
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            await session.execute(
                delete(AgentArtifact).where(
                    or_(
                        AgentArtifact.agent_run_id == run_id,
                        and_(
                            AgentArtifact.agent_id == run.agent_id,
                            AgentArtifact.agent_run_id.is_(None),
                        ),
                    )
                )
            )
            await session.commit()
    except Exception:
        logger.debug("failed to clear artifacts for run %s", run_id, exc_info=True)
