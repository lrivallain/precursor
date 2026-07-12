"""System-footprint statistics for the settings usage tab.

Reports the database size and per-table breakdown, the on-disk blob store size,
GitHub issue counts (open vs closed) when a repo + token are configured, and a
count of the top-level entities (topics, chats, agents, workspaces).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.models import Base
from precursor.backend.schemas.stats import (
    BlobStats,
    DatabaseStats,
    EntityCounts,
    IssueStats,
    SystemStats,
    TableStat,
)
from precursor.backend.services.app_settings import resolve_global_github_repo
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.github_client import (
    GitHubClient,
    GitHubRepoNotAccessibleError,
)

logger = logging.getLogger(__name__)


async def compute_system_stats(session: AsyncSession) -> SystemStats:
    database = await _database_stats(session)
    counts = {t.name: t.row_count for t in database.tables}
    return SystemStats(
        database=database,
        blobs=_blob_stats(),
        issues=await _issue_stats(session),
        entities=EntityCounts(
            topics=counts.get("topics", 0),
            chats=counts.get("chats", 0),
            agents=counts.get("agent_sessions", 0),
            workspaces=counts.get("workspaces", 0),
        ),
    )


async def _database_stats(session: AsyncSession) -> DatabaseStats:
    from precursor.backend.db import engine

    sync_engine = engine.sync_engine
    engine_name = sync_engine.dialect.name

    # Per-table row counts across every mapped table.
    row_counts: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        count = await session.scalar(select(func.count()).select_from(table))
        row_counts[table.name] = int(count or 0)

    sizes = await _sqlite_table_sizes(session) if engine_name == "sqlite" else {}

    tables = [
        TableStat(name=name, row_count=row_counts[name], size_bytes=sizes.get(name))
        for name in sorted(row_counts)
    ]

    path, size_bytes = _database_file_size(sync_engine.url.database, engine_name)
    return DatabaseStats(engine=engine_name, size_bytes=size_bytes, path=path, tables=tables)


async def _sqlite_table_sizes(session: AsyncSession) -> dict[str, int]:
    """Bytes per table via SQLite's ``dbstat`` vtab, or ``{}`` if unavailable.

    ``dbstat`` requires the ``SQLITE_ENABLE_DBSTAT_VTAB`` build option, which
    isn't guaranteed, so a failure here is expected and non-fatal.
    """
    try:
        result = await session.execute(
            text("SELECT name, SUM(pgsize) AS size FROM dbstat GROUP BY name")
        )
    except Exception:  # dbstat may not be compiled into this SQLite build
        logger.debug("dbstat unavailable; skipping per-table sizes", exc_info=True)
        return {}
    return {name: int(size or 0) for name, size in result.all()}


def _database_file_size(db_path: str | None, engine_name: str) -> tuple[str | None, int | None]:
    """Return ``(display_path, total_bytes)`` for a file-backed DB.

    For SQLite this sums the main file plus the WAL and SHM sidecar files (they
    hold uncheckpointed pages and can be sizeable under WAL journaling).
    Non-file engines (e.g. Postgres) report ``(None, None)``.
    """
    if engine_name != "sqlite":
        return None, None
    if not db_path or db_path == ":memory:":
        return None, None
    main = Path(db_path)
    total = 0
    found = False
    for candidate in (main, main.with_name(main.name + "-wal"), main.with_name(main.name + "-shm")):
        try:
            total += candidate.stat().st_size
            found = True
        except OSError:
            continue
    return str(main), (total if found else None)


def _blob_stats() -> BlobStats:
    root = Path(get_settings().blobs_dir)
    if not root.exists():
        return BlobStats(count=0, size_bytes=0, path=str(root))
    count = 0
    size = 0
    for entry in root.rglob("*"):
        if not entry.is_file() or entry.name.endswith(".tmp"):
            continue
        try:
            size += entry.stat().st_size
        except OSError:  # pragma: no cover - defensive
            continue
        count += 1
    return BlobStats(count=count, size_bytes=size, path=str(root))


async def _issue_stats(session: AsyncSession) -> IssueStats:
    repo = await resolve_global_github_repo(session)
    token = await resolve_github_token(session)
    if not repo or not token:
        return IssueStats(configured=False, repo=repo or None)

    client = GitHubClient(token=token)
    try:
        open_count, closed_count = await client.count_issues_by_state(repo)
    except GitHubRepoNotAccessibleError:
        # Expected when the configured repo is private/nonexistent for this
        # token — surface a short, friendly note rather than a raw API error.
        return IssueStats(
            configured=True, repo=repo, error="Repository not found or not accessible"
        )
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.debug("issue count lookup failed for %s: %s", repo, exc)
        return IssueStats(configured=True, repo=repo, error="Couldn't reach GitHub")
    finally:
        await client.aclose()

    return IssueStats(configured=True, repo=repo, open=open_count, closed=closed_count)
