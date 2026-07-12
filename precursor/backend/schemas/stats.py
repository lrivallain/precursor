"""Usage-statistics schemas — aggregated token consumption read models."""

from __future__ import annotations

from pydantic import BaseModel


class UsageBucket(BaseModel):
    """Token usage accumulated over a single time bucket.

    ``period`` is the bucket key: an ISO week (``YYYY-Www``), a month
    (``YYYY-MM``), a year (``YYYY``), or ``"all"`` for the grand total.
    """

    period: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    message_count: int = 0


class UsageStats(BaseModel):
    """Global token usage across every topic and chat.

    The per-period lists are ordered chronologically (oldest first).
    """

    totals: UsageBucket
    weekly: list[UsageBucket] = []
    monthly: list[UsageBucket] = []
    yearly: list[UsageBucket] = []


class TableStat(BaseModel):
    """Per-table statistics for the application database."""

    name: str
    row_count: int
    # On-disk bytes attributed to the table (SQLite ``dbstat``). Null when the
    # engine can't attribute size per table (e.g. ``dbstat`` unavailable).
    size_bytes: int | None = None


class DatabaseStats(BaseModel):
    """Size and per-table breakdown of the application database."""

    engine: str
    # Total on-disk size in bytes (main file + WAL/SHM for SQLite). Null when
    # the size can't be determined (e.g. a remote Postgres server).
    size_bytes: int | None = None
    path: str | None = None
    tables: list[TableStat] = []


class BlobStats(BaseModel):
    """Size and count of the content-addressed attachment blob store."""

    count: int = 0
    size_bytes: int = 0
    path: str | None = None


class IssueStats(BaseModel):
    """Open/closed GitHub issue counts for the configured repository.

    ``configured`` is false when no repo + token pair is available; the counts
    are then null. ``error`` carries a short message when the lookup failed.
    """

    configured: bool = False
    repo: str | None = None
    open: int | None = None
    closed: int | None = None
    error: str | None = None


class EntityCounts(BaseModel):
    """Counts of the top-level conversation/work entities."""

    topics: int = 0
    chats: int = 0
    agents: int = 0
    workspaces: int = 0


class SystemStats(BaseModel):
    """System-level footprint stats surfaced in the settings usage tab."""

    database: DatabaseStats
    blobs: BlobStats
    issues: IssueStats
    entities: EntityCounts
