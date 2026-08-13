"""workflow step policies, approval, tokens and watchdog

Five related capabilities land together because they all reshape how a run is
driven and traced:

* ``workflow_steps.instructions`` — a per-step mandate appended to the agent's own
  objective, so one agent row is reusable with a different brief per step.
* ``workflow_steps.on_error`` / ``max_retries`` / ``retry_count`` — per-step failure
  policy (stop / retry N / carry on) instead of "any failure kills the run".
* ``workflows.step_timeout_seconds`` — stall watchdog; a step stuck past this is
  cancelled and put through its failure policy.
* ``workflow_runs.total_input_tokens`` / ``total_output_tokens`` and the matching
  per-attempt columns on ``workflow_run_steps`` (plus internal baselines used to
  compute each attempt's delta) — cost roll-up per step and per run.

The ``approval`` step kind and the ``awaiting_approval`` statuses need no DDL:
``kind`` and ``status`` are already free-form strings.

Revision ID: e7c2f4a9b8d1
Revises: d5e9b0c1a2f3
Create Date: 2026-08-21 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7c2f4a9b8d1"
down_revision = "d5e9b0c1a2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-step mandate + failure policy.
    op.add_column("workflow_steps", sa.Column("instructions", sa.Text(), nullable=True))
    op.add_column(
        "workflow_steps",
        sa.Column("on_error", sa.String(length=16), nullable=False, server_default="fail"),
    )
    op.add_column(
        "workflow_steps",
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workflow_steps",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # Stall watchdog.
    op.add_column("workflows", sa.Column("step_timeout_seconds", sa.Integer(), nullable=True))

    # Cost roll-up: per run…
    op.add_column(
        "workflow_runs",
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    # …and per step attempt (with the baselines used to compute the delta).
    for col in ("input_tokens", "output_tokens", "token_baseline_in", "token_baseline_out"):
        op.add_column(
            "workflow_run_steps",
            sa.Column(col, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for col in ("input_tokens", "output_tokens", "token_baseline_in", "token_baseline_out"):
        op.drop_column("workflow_run_steps", col)
    op.drop_column("workflow_runs", "total_output_tokens")
    op.drop_column("workflow_runs", "total_input_tokens")
    op.drop_column("workflows", "step_timeout_seconds")
    op.drop_column("workflow_steps", "retry_count")
    op.drop_column("workflow_steps", "max_retries")
    op.drop_column("workflow_steps", "on_error")
    op.drop_column("workflow_steps", "instructions")
