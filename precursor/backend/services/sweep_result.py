"""Shared result type for retention sweeps.

Kept in its own module so every sweep can report a uniform shape without the
storage-cleanup orchestrator (which imports the sweeps) creating an import
cycle.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SweepResult:
    """Rows and payload bytes a sweep removed — or would remove, when previewing.

    ``bytes`` is an estimate of the reclaimed *content*, not of the on-disk delta:
    SQLite only returns freed pages to the OS on ``VACUUM``.
    """

    rows: int = 0
    bytes: int = 0

    def __add__(self, other: SweepResult) -> SweepResult:
        return SweepResult(rows=self.rows + other.rows, bytes=self.bytes + other.bytes)
