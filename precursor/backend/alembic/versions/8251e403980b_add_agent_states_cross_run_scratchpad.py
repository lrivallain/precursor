"""add agent_states cross-run scratchpad

Adds ``agent_states`` — an agent's private, durable key/value scratchpad that
**survives re-runs**, unlike ``agent_artifacts`` (wiped at the start of every
fresh run) and unlike ``memories`` (global and injected into every turn). It's
what a scheduled or webhook-triggered agent uses to remember a cursor, a set of
seen ids, or a counter between runs.

Revision ID: 8251e403980b
Revises: 4fd7f3a6c9e4
Create Date: 2026-08-13 17:14:41.625491

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8251e403980b"
down_revision: str | None = "4fd7f3a6c9e4"
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
    # CREATE TABLE and the version stamp (e.g. stopping a dev server mid-startup)
    # leaves the table present but the revision unstamped — and every later boot
    # then dies on "table agent_states already exists", wedging the app. The
    # table and its index are two statements, so guard each: the crash window
    # sits between them too.
    if not _has_table("agent_states"):
        op.create_table(
            "agent_states",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("agent_id", sa.Integer(), nullable=False),
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
            sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("agent_id", "key", name="uq_agent_state_agent_key"),
        )
    if not _has_index("agent_states", "ix_agent_states_agent_id"):
        op.create_index("ix_agent_states_agent_id", "agent_states", ["agent_id"])


def downgrade() -> None:
    if _has_index("agent_states", "ix_agent_states_agent_id"):
        op.drop_index("ix_agent_states_agent_id", table_name="agent_states")
    if _has_table("agent_states"):
        op.drop_table("agent_states")
