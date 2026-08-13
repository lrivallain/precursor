"""workflow context sourcing, capability toggles and role

Four related additions:

* ``agent_sessions.use_mcp`` / ``use_skills`` / ``use_memory`` — what an agent may
  draw on. All default on (today's behaviour); turning them off is a lever on
  both cost and focus.
* ``workflow_steps.use_mcp`` / ``use_skills`` / ``use_memory`` — nullable per-step
  overrides (null = inherit the agent's own setting).
* ``workflow_steps.context_mode`` / ``context_sources`` — how a step is fed:
  ``auto`` (previous producer + blackboard, the default), ``selected`` (only the
  listed 0-based step positions), or ``none``.
* ``workflows.role_id`` — an Assistant Role applied to every step's agent for the
  duration of a run, so a pipeline speaks with one voice without stamping the
  persona onto the shared agent rows.

Revision ID: a9d4e7f21c60
Revises: f3b8d1c05a92
Create Date: 2026-08-21 16:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9d4e7f21c60"
down_revision = "f3b8d1c05a92"
branch_labels = None
depends_on = None

_CAPABILITIES = ("use_mcp", "use_skills", "use_memory")


def upgrade() -> None:
    # Agent-level capability toggles — on by default so nothing changes for
    # existing agents.
    for col in _CAPABILITIES:
        op.add_column(
            "agent_sessions",
            sa.Column(col, sa.Boolean(), nullable=False, server_default="1"),
        )

    # Per-step overrides (null = inherit the agent).
    for col in _CAPABILITIES:
        op.add_column("workflow_steps", sa.Column(col, sa.Boolean(), nullable=True))

    op.add_column(
        "workflow_steps",
        sa.Column("context_mode", sa.String(length=16), nullable=False, server_default="auto"),
    )
    op.add_column(
        "workflow_steps", sa.Column("context_sources", sa.String(length=200), nullable=True)
    )

    # Workflow-wide Assistant Role.
    with op.batch_alter_table("workflows") as batch:
        batch.add_column(sa.Column("role_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_workflows_role_id_roles", "roles", ["role_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_workflows_role_id", "workflows", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_workflows_role_id", table_name="workflows")
    with op.batch_alter_table("workflows") as batch:
        batch.drop_constraint("fk_workflows_role_id_roles", type_="foreignkey")
        batch.drop_column("role_id")
    op.drop_column("workflow_steps", "context_sources")
    op.drop_column("workflow_steps", "context_mode")
    for col in _CAPABILITIES:
        op.drop_column("workflow_steps", col)
        op.drop_column("agent_sessions", col)
