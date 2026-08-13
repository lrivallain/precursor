"""Tests for data migrations whose correctness is a judgement call.

Schema migrations are exercised implicitly — every test brings the scratch DB to
head. A migration that *rewrites existing rows* is different: it encodes a rule
about what historical data means, and getting that rule wrong silently corrupts
what it was meant to repair. Those get pinned here.

Each test builds the minimal tables the migration touches in a throwaway SQLite
file and runs the revision's ``upgrade()`` against it directly, so it exercises
the real SQL rather than a paraphrase of it.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import sqlalchemy as sa

_VERSIONS = Path(__file__).resolve().parents[1] / "precursor" / "backend" / "alembic" / "versions"


def _load_revision(revision: str) -> Any:
    """Import one migration module by revision id."""
    matches = [p for p in _VERSIONS.glob("*.py") if p.name.startswith(revision)]
    assert matches, f"no migration file for revision {revision}"
    spec = importlib.util.spec_from_file_location(f"_migration_{revision}", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- c8f1e60a3b74: repair run steps duplicated by the advance race -----------

# (id, run, position, started_at, finished_at, input_tokens, output_tokens, status)
_STEP_COLUMNS = "id, run_id, position, started_at, finished_at, input_tokens, output_tokens, status"


def _run_repair(rows: list[tuple]) -> tuple[dict[int, tuple[str, int]], tuple[int, int]]:
    """Run the repair migration over ``rows``.

    Returns ``({row_id: (status, input_tokens)}, (run_input, run_output))``.
    """
    migration = _load_revision("c8f1e60a3b74")

    # A file SQLite needs to reopen, so it outlives the handle deliberately.
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE workflow_run_steps ("
                    "  id INTEGER PRIMARY KEY, run_id INT, position INT,"
                    "  started_at TEXT, finished_at TEXT,"
                    "  input_tokens INT, output_tokens INT, status TEXT)"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE TABLE workflow_runs ("
                    "  id INTEGER PRIMARY KEY,"
                    "  total_input_tokens INT, total_output_tokens INT)"
                )
            )
            # Seed the rollup with the inflated figures the buggy incremental
            # accounting would have left, so a no-op repair is detectable.
            inflated_in = sum(r[5] for r in rows)
            inflated_out = sum(r[6] for r in rows)
            conn.execute(
                sa.text("INSERT INTO workflow_runs VALUES (1, :i, :o)"),
                {"i": inflated_in, "o": inflated_out},
            )
            for row in rows:
                conn.execute(
                    sa.text(
                        f"INSERT INTO workflow_run_steps ({_STEP_COLUMNS}) "
                        "VALUES (:a, :b, :c, :d, :e, :f, :g, :h)"
                    ),
                    dict(zip("abcdefgh", row, strict=True)),
                )

        with engine.begin() as conn, mock.patch.object(migration.op, "get_bind", return_value=conn):
            migration.upgrade()

        with engine.connect() as conn:
            steps = {
                r[0]: (r[1], r[2])
                for r in conn.execute(
                    sa.text("SELECT id, status, input_tokens FROM workflow_run_steps")
                )
            }
            totals = conn.execute(
                sa.text("SELECT total_input_tokens, total_output_tokens FROM workflow_runs")
            ).one()
        return steps, (totals[0], totals[1])
    finally:
        engine.dispose()
        os.unlink(db_path)


def test_repair_migration_keeps_a_fast_retry_and_drops_only_real_duplicates() -> None:
    """Closeness in time alone must not condemn a row.

    The race opened attempts milliseconds apart, so proximity is how they're
    found — but an ``on_error=retry`` whose first attempt fails after two seconds
    is inside that window too, and it billed real tokens. Superseding it would
    delete spend from the rollup, which is the very under-reporting this
    migration exists to correct. Proximity only nominates; duplicate *evidence*
    (an orphaned row, or an exact spend twin) condemns.
    """
    steps, (total_in, _total_out) = _run_repair(
        [
            # A legitimate fast retry: 2s apart, both finished, distinct spend.
            (1, 1, 0, "2026-08-13T10:00:00", "2026-08-13T10:00:02", 100_000, 500, "failed"),
            (2, 1, 0, "2026-08-13T10:00:02", "2026-08-13T10:00:40", 200_000, 900, "completed"),
            # A race pair: the loser never finalized, so it billed nothing.
            (3, 1, 1, "2026-08-13T10:01:00.100", None, 0, 0, "running"),
            (4, 1, 1, "2026-08-13T10:01:00.300", "2026-08-13T10:05:00", 300_000, 700, "completed"),
            # A race pair that both finalized, measuring the same turn twice.
            (5, 1, 2, "2026-08-13T10:06:00.100", "2026-08-13T10:09:00", 400_000, 800, "completed"),
            (6, 1, 2, "2026-08-13T10:06:00.200", "2026-08-13T10:09:00", 400_000, 800, "completed"),
            # A gate loop-back, minutes apart — nowhere near the window.
            (7, 1, 3, "2026-08-13T10:10:00", "2026-08-13T10:12:00", 50_000, 100, "failed"),
            (8, 1, 3, "2026-08-13T10:18:00", "2026-08-13T10:20:00", 60_000, 200, "completed"),
        ]
    )

    # The fast retry keeps its own row *and* its spend.
    assert steps[1] == ("failed", 100_000)
    assert steps[2] == ("completed", 200_000)
    # The orphan and the exact twin are the duplicates, and only they.
    assert steps[3][0] == "superseded"
    assert steps[4][0] == "completed"
    assert steps[5][0] == "superseded"
    assert steps[6][0] == "completed"
    # The slow loop-back is untouched.
    assert steps[7] == ("failed", 50_000)
    assert steps[8] == ("completed", 60_000)

    # The rollup counts every real attempt exactly once — the duplicated turn's
    # 400,000 comes out, nothing else does.
    assert total_in == 100_000 + 200_000 + 300_000 + 400_000 + 50_000 + 60_000


def test_repair_migration_closes_orphans_so_nothing_shows_as_in_flight() -> None:
    """A losing advance's row must not read as a step that never finished."""
    steps, _totals = _run_repair(
        [
            (1, 1, 3, "2026-08-13T08:46:30.375", None, 0, 0, "running"),
            (2, 1, 3, "2026-08-13T08:46:30.395", None, 0, 0, "running"),
            (
                3,
                1,
                3,
                "2026-08-13T08:46:30.590",
                "2026-08-13T08:51:02",
                1_509_537,
                900,
                "completed",
            ),
        ]
    )

    # The attempt that did the work survives; the two that lost the race don't.
    assert steps[1][0] == "superseded"
    assert steps[2][0] == "superseded"
    assert steps[3] == ("completed", 1_509_537)


def test_repair_migration_leaves_an_unfinished_lone_attempt_alone() -> None:
    """A run stopped mid-step has one open row, and it is genuinely in flight."""
    steps, _totals = _run_repair(
        [
            (1, 1, 0, "2026-08-13T10:00:00", "2026-08-13T10:01:00", 1_000, 10, "completed"),
            (2, 1, 1, "2026-08-13T10:01:00", None, 0, 0, "running"),
        ]
    )

    assert steps[2][0] == "running"


def test_repair_migration_spares_rows_predating_token_accounting() -> None:
    """Two zeroed rows are not evidence of anything.

    ``input_tokens``/``output_tokens`` default to 0, so traces written before
    per-attempt accounting existed all look identical. Treating that as twin
    evidence would supersede real attempts on the strength of a column that was
    never populated — and they contribute nothing to the rollup anyway.
    """
    steps, (total_in, _total_out) = _run_repair(
        [
            (1, 1, 0, "2026-08-13T10:00:00", "2026-08-13T10:00:01", 0, 0, "failed"),
            (2, 1, 0, "2026-08-13T10:00:02", "2026-08-13T10:00:30", 0, 0, "completed"),
        ]
    )

    assert steps[1][0] == "failed"
    assert steps[2][0] == "completed"
    assert total_in == 0


def test_repair_migration_is_a_no_op_on_an_undamaged_database() -> None:
    """Upgrading a healthy install must not move a single number."""
    steps, (total_in, total_out) = _run_repair(
        [
            (1, 1, 0, "2026-08-13T10:00:00", "2026-08-13T10:05:00", 1_000, 10, "completed"),
            (2, 1, 1, "2026-08-13T10:05:00", "2026-08-13T10:09:00", 2_000, 20, "completed"),
        ]
    )

    assert steps[1] == ("completed", 1_000)
    assert steps[2] == ("completed", 2_000)
    assert (total_in, total_out) == (3_000, 30)
