"""workflow run history

Adds durable per-run, per-step trace history to workflows so the Workflows page
can browse and compare previous runs — and, crucially, show what *input* each
step received and what it *produced*, even after ``clear_artifacts`` wipes the
live agents' artifacts for the next run.

* ``workflow_runs`` — one row per execution (run number, status, trigger, timing,
  outcome).
* ``workflow_run_steps`` — one row per step attempt within a run (position, kind,
  label + agent snapshot, input context, output, gate verdict, timing).
* ``workflows.current_run_id`` — the run the coordinator is currently appending
  traces to.

Revision ID: c3f7a1b2d4e8
Revises: a7c93e21f4d8
Create Date: 2026-08-20 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3f7a1b2d4e8"
down_revision = "a7c93e21f4d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "run_number", name="uq_workflow_run_number"),
    )
    op.create_index("ix_workflow_runs_workflow_id", "workflow_runs", ["workflow_id"], unique=False)
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"], unique=False)

    op.create_table(
        "workflow_run_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="task"),
        sa.Column("label", sa.String(length=200), nullable=False, server_default="Step"),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("input_context", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("gate_verdict", sa.String(length=8), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_run_steps_run_id", "workflow_run_steps", ["run_id"], unique=False)

    # A pointer from the workflow row to its in-flight run. Added via batch so
    # SQLite gets a real FK constraint on the existing table.
    with op.batch_alter_table("workflows") as batch:
        batch.add_column(sa.Column("current_run_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_workflows_current_run_id",
            "workflow_runs",
            ["current_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("workflows") as batch:
        batch.drop_constraint("fk_workflows_current_run_id", type_="foreignkey")
        batch.drop_column("current_run_id")
    op.drop_index("ix_workflow_run_steps_run_id", table_name="workflow_run_steps")
    op.drop_table("workflow_run_steps")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workflow_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
