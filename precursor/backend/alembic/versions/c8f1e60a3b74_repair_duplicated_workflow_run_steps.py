"""repair workflow run steps duplicated by the advance race

Revision ID: c8f1e60a3b74
Revises: d686a8d80c27
Create Date: 2026-08-13 13:10:00.000000

Until this revision the manager's completion seam enqueued a workflow advance on
*every* event an already-resting agent emitted, and those advances ran
concurrently with no lock. Each one re-entered the same step, so one logical step
entry could open several ``workflow_run_steps`` rows and launch the step's agent
several times for real.

The traces that survived show two shapes: rows stuck at ``running`` with no
``finished_at`` (a losing advance's row, which renders as a step forever in
flight), and duplicate *finished* rows carrying identical token deltas, whose
spend was added to the run rollup once per row — inflating reported totals by
roughly 40% on the runs that hit it.

Finding them takes two conditions, not one. Rows within a ``(run_id, position)``
starting within :data:`_DUPLICATE_WINDOW_SECONDS` of each other are *candidates*
— but that window alone is not proof. It compares ``started_at`` to
``started_at``, so a legitimate ``on_error=retry`` whose first attempt failed
after two seconds falls inside it too, and superseding that would delete real
spend from the rollup: exactly the under-reporting this migration exists to fix.

So a candidate is only superseded when it also carries duplicate *evidence*:

* it is **orphaned** (``finished_at IS NULL``) — a losing advance's row, which
  never finalized and never billed anything; or
* it is an **exact spend twin** of the attempt that survived (identical
  ``input_tokens`` and ``output_tokens``, and non-zero) — the same turn measured
  twice, which is what inflated the rollup.

A fast retry is neither: it finished, and it billed its own distinct amount. The
attempt that actually ran always survives, its twins are marked ``superseded``
and closed, then each run's token totals are recomputed from what remains.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f1e60a3b74"
down_revision: str | None = "d686a8d80c27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# How close two attempts at the same position have to start to be the same entry.
# Generous next to the ~200ms the observed races produced, and far below the
# duration of any real turn.
_DUPLICATE_WINDOW_SECONDS = 5.0


def _parse(value: object) -> float | None:
    """Seconds-since-epoch for a timestamp column, across SQLite/Postgres."""
    if value is None:
        return None
    if isinstance(value, str):
        # SQLite hands back ISO text.
        try:
            from datetime import datetime

            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    timestamp = getattr(value, "timestamp", None)
    return timestamp() if callable(timestamp) else None


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.text(
            "SELECT id, run_id, position, started_at, finished_at, "
            "       input_tokens, output_tokens "
            "FROM workflow_run_steps ORDER BY run_id, position, id"
        )
    ).fetchall()

    # Group each position's rows into clusters of near-simultaneous starts. Rows
    # are compared against the cluster's *first* start, so a genuine later
    # attempt opens a new cluster rather than extending the previous one.
    clusters: list[list[tuple]] = []
    current: list[tuple] = []
    anchor_key: tuple[int, int] | None = None
    anchor_at: float | None = None
    for row in rows:
        key = (row[1], row[2])
        started = _parse(row[3])
        same_entry = (
            key == anchor_key
            and started is not None
            and anchor_at is not None
            and abs(started - anchor_at) <= _DUPLICATE_WINDOW_SECONDS
        )
        if same_entry:
            current.append(row)
            continue
        if current:
            clusters.append(current)
        current = [row]
        anchor_key, anchor_at = key, started
    if current:
        clusters.append(current)

    superseded: list[int] = []
    for cluster in clusters:
        if len(cluster) < 2:
            # A single attempt — including a run interrupted mid-step, whose open
            # row is genuinely in flight and must be left alone.
            continue
        # Keep the one that did the work: it finished, and it carries the spend.
        # (The losers are the advances that were beaten to the finalize.)
        winner = max(cluster, key=lambda r: (r[4] is not None, (r[5] or 0) + (r[6] or 0), r[0]))
        winner_spend = (winner[5] or 0, winner[6] or 0)
        for row in cluster:
            if row[0] == winner[0]:
                continue
            spend = (row[5] or 0, row[6] or 0)
            # Closeness in time is only a candidate signal — see the module
            # docstring. Require actual evidence of duplication before touching
            # a row, so a fast legitimate retry keeps its distinct spend.
            orphaned = row[4] is None
            spend_twin = spend == winner_spend and sum(spend) > 0
            if orphaned or spend_twin:
                superseded.append(row[0])

    for row_id in superseded:
        bind.execute(
            sa.text(
                "UPDATE workflow_run_steps "
                "SET status = 'superseded', "
                "    finished_at = COALESCE(finished_at, started_at) "
                "WHERE id = :id"
            ),
            {"id": row_id},
        )

    # Whatever the duplicates contributed has to come back out of the rollup, so
    # rebuild every run's totals from the rows that are left.
    bind.execute(
        sa.text(
            "UPDATE workflow_runs SET "
            "  total_input_tokens = COALESCE(("
            "    SELECT SUM(input_tokens) FROM workflow_run_steps"
            "    WHERE run_id = workflow_runs.id AND status <> 'superseded'"
            "  ), 0), "
            "  total_output_tokens = COALESCE(("
            "    SELECT SUM(output_tokens) FROM workflow_run_steps"
            "    WHERE run_id = workflow_runs.id AND status <> 'superseded'"
            "  ), 0)"
        )
    )


def downgrade() -> None:
    # Data-only repair. The pre-repair rollups were wrong and the duplicate rows
    # they were computed from are indistinguishable from real attempts once
    # restored, so there is nothing meaningful to put back.
    pass
