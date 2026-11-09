"""Building blocks shared by the age-limited retention sweeps.

Each sweep (tool results, Live transcripts, workflow step inputs, agent events)
follows the same shape: resolve a retention window, select the expired rows,
measure them so the settings UI can preview the effect (``dry_run``), then
replace a large column with a placeholder or delete the rows, and report a
``SweepResult``. The sweep modules keep their own criteria, docs and log lines;
the mechanics live here.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import ColumnElement

from precursor.backend.services.sweep_result import SweepResult

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def cutoff(retention_days: int) -> datetime:
    """The instant before which rows are expired for a ``retention_days`` window."""
    return datetime.now(UTC) - timedelta(days=retention_days)


def rowcount(result: Result[Any]) -> int:
    """Rows affected by a bulk ``UPDATE`` / ``DELETE``."""
    return int(cast("CursorResult[Any]", result).rowcount or 0)


async def measure(
    session: AsyncSession, column: Any, criteria: tuple[ColumnElement[bool], ...]
) -> SweepResult:
    """Rows matching ``criteria`` and the total length of ``column`` across them."""
    measured = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(func.length(column)), 0)).where(*criteria)
        )
    ).one()
    return SweepResult(rows=int(measured[0] or 0), bytes=int(measured[1] or 0))


async def replace_column(
    session: AsyncSession,
    column: InstrumentedAttribute[Any],
    criteria: tuple[ColumnElement[bool], ...],
    *,
    replacement: str | None,
    dry_run: bool,
) -> SweepResult:
    """Set ``column`` to ``replacement`` on the matching rows; report the effect.

    ``bytes`` is what the rows held minus what the replacement keeps. With
    ``dry_run`` nothing is written and ``rows`` is the number that would change.
    The caller commits.
    """
    found = await measure(session, column, criteria)
    kept = found.rows * len(replacement or "")
    freed = max(found.bytes - kept, 0)
    if dry_run or not found.rows:
        return SweepResult(rows=found.rows, bytes=freed)
    model = column.class_
    result = await session.execute(update(model).where(*criteria).values({column.key: replacement}))
    return SweepResult(rows=rowcount(result), bytes=freed)


async def delete_rows(
    session: AsyncSession,
    size_column: InstrumentedAttribute[Any],
    criteria: tuple[ColumnElement[bool], ...],
    *,
    dry_run: bool,
) -> SweepResult:
    """Delete the matching rows of ``size_column``'s table; report the effect.

    ``bytes`` is the total length of ``size_column`` across them. The caller
    commits.
    """
    found = await measure(session, size_column, criteria)
    if dry_run or not found.rows:
        return found
    result = await session.execute(delete(size_column.class_).where(*criteria))
    return SweepResult(rows=rowcount(result), bytes=found.bytes)
