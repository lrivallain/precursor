"""add workflow_states pipeline memory

Adds ``workflow_states`` — a workflow's own durable key/value memory, written by
one step and read by another (or by the next run). The agent-level counterpart
(``agent_states``) can't serve this: a step points at a *reusable* agent, so a
key written under the agent's scope is shared with every other pipeline using
that agent, and an ``inline`` agent's scratchpad dies with its step.

Revision ID: a7f3c91b5d20
Revises: 8251e403980b
Create Date: 2026-08-14 09:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f3c91b5d20"
down_revision: str | None = "8251e403980b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _has_index(table: str, index: str) -> bool:
    if not _has_table(table):
        return False
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    # Idempotent: SQLite treats DDL as non-transactional, so a crash between the
    # CREATE TABLE and the version stamp leaves the table present with the
    # revision unstamped, and every later boot would die on "table already
    # exists". The table and its index are two statements, so guard each.
    if not _has_table("workflow_states"):
        op.create_table(
            "workflow_states",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(length=120), nullable=False),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workflow_id", "key", name="uq_workflow_state_key"),
        )
    if not _has_index("workflow_states", "ix_workflow_states_workflow_id"):
        op.create_index("ix_workflow_states_workflow_id", "workflow_states", ["workflow_id"])


def downgrade() -> None:
    if _has_index("workflow_states", "ix_workflow_states_workflow_id"):
        op.drop_index("ix_workflow_states_workflow_id", table_name="workflow_states")
    if _has_table("workflow_states"):
        op.drop_table("workflow_states")
