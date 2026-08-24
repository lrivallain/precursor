"""Storage cockpit — on-demand retention sweeps and database compaction.

The tickers apply retention on a daily cadence, which is the right default but a
poor answer to "my database is 126 MB *now*". This module exposes the same
sweeps as named, user-triggerable targets with a **preview** mode, plus the
compaction step that actually returns freed pages to the filesystem.

Compaction matters more than it looks: Precursor runs SQLite with
``auto_vacuum=NONE``, so deleting rows only marks pages reusable — the file
never shrinks on its own. A sweep followed by ``VACUUM`` is what a user
experiences as "my database got smaller".
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from precursor.backend.services.agent_event_recap import recap_archived_events
from precursor.backend.services.agent_event_retention import prune_agent_events
from precursor.backend.services.live_transcript_retention import prune_expired_live_transcripts
from precursor.backend.services.sweep_result import SweepResult
from precursor.backend.services.tool_result_retention import prune_expired_tool_results

logger = logging.getLogger(__name__)

Sweep = Callable[..., Awaitable[SweepResult]]


@dataclass(frozen=True)
class CleanupTarget:
    """One user-facing cleanup action in the storage cockpit."""

    key: str
    label: str
    # What the sweep removes, and the setting that governs it — shown in the UI
    # so "Clean now" is never a mystery button.
    description: str
    setting: str
    table: str
    run: Sweep


TARGETS: tuple[CleanupTarget, ...] = (
    CleanupTarget(
        key="agent_events",
        label="Agent timelines",
        description=(
            "Drops the archived event trace of agents that are no longer running. "
            "Agents keep their result, artifacts and messages."
        ),
        setting="agent_event_retention_days",
        table="agent_events",
        run=prune_agent_events,
    ),
    CleanupTarget(
        key="oversized_events",
        label="Oversized agent events",
        description=(
            "Trims archived event payloads that predate the current size caps. "
            "Every timeline node is kept — only long captured blobs are shortened."
        ),
        setting="—",
        table="agent_events",
        run=recap_archived_events,
    ),
    CleanupTarget(
        key="tool_results",
        label="Tool results",
        description=(
            "Replaces the body of old tool results with a short placeholder. "
            "The conversation structure is preserved."
        ),
        setting="tool_result_retention_days",
        table="messages",
        run=prune_expired_tool_results,
    ),
    CleanupTarget(
        key="live_transcripts",
        label="Live transcripts",
        description=(
            "Deletes raw transcript segments of ended meetings. "
            "Summaries, insights and notes are kept."
        ),
        setting="live_transcript_retention_days",
        table="meeting_segments",
        run=prune_expired_live_transcripts,
    ),
)

_BY_KEY = {t.key: t for t in TARGETS}


def get_target(key: str) -> CleanupTarget | None:
    return _BY_KEY.get(key)


async def preview_all() -> dict[str, SweepResult]:
    """Measure every target without deleting anything.

    A failing target reports zero rather than sinking the whole panel — the
    cockpit is a diagnostic surface and must still render.
    """
    out: dict[str, SweepResult] = {}
    for target in TARGETS:
        try:
            out[target.key] = await target.run(dry_run=True)
        except Exception:
            logger.warning("Cleanup preview failed for %s", target.key, exc_info=True)
            out[target.key] = SweepResult()
    return out


async def run_target(key: str, *, dry_run: bool = False) -> SweepResult:
    """Run one named cleanup target."""
    target = _BY_KEY[key]
    return await target.run(dry_run=dry_run)


@dataclass(frozen=True)
class VacuumResult:
    """On-disk size around a compaction, in bytes."""

    supported: bool
    size_before: int | None = None
    size_after: int | None = None
    error: str | None = None

    @property
    def reclaimed(self) -> int:
        if self.size_before is None or self.size_after is None:
            return 0
        return max(self.size_before - self.size_after, 0)


def _sqlite_total_bytes(db_path: str) -> int:
    """Main file plus the WAL/SHM sidecars, which VACUUM also collapses."""
    main = Path(db_path)
    total = 0
    for candidate in (main, main.with_name(main.name + "-wal"), main.with_name(main.name + "-shm")):
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
    return total


async def compact_database() -> VacuumResult:
    """``VACUUM`` the database so freed pages return to the filesystem.

    SQLite only. VACUUM cannot run inside a transaction, so this reaches for a
    dedicated autocommit connection rather than a request-scoped session.
    """
    from precursor.backend.db import engine

    if engine.sync_engine.dialect.name != "sqlite":
        return VacuumResult(supported=False, error="Compaction is only supported on SQLite")
    db_path = engine.sync_engine.url.database
    if not db_path or db_path == ":memory:":
        return VacuumResult(supported=False, error="Database is not file-backed")

    before = _sqlite_total_bytes(db_path)
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            # Checkpoint on both sides of the VACUUM. Before, so the WAL's pages
            # are folded into the file being rebuilt; after, because VACUUM in
            # WAL mode writes the *entire* rebuilt database through the WAL —
            # skipping the second checkpoint leaves a WAL bigger than the
            # database and the file grows instead of shrinking.
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            await conn.execute(text("VACUUM"))
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    except Exception as exc:
        logger.warning("Database compaction failed", exc_info=True)
        return VacuumResult(supported=True, size_before=before, error=str(exc))
    after = _sqlite_total_bytes(db_path)
    logger.info("Compacted database: %d → %d bytes", before, after)
    return VacuumResult(supported=True, size_before=before, size_after=after)
