"""add agent archiving and message agent link

Revision ID: 7ef20a82eb10
Revises: 0fd63760742a
Create Date: 2026-06-23 17:15:10.758362

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7ef20a82eb10"
down_revision: str | None = "0fd63760742a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("agent_session_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_messages_agent_session_id"), ["agent_session_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_messages_agent_session_id",
            "agent_sessions",
            ["agent_session_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_constraint("fk_messages_agent_session_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_messages_agent_session_id"))
        batch_op.drop_column("agent_session_id")
    op.drop_column("agent_sessions", "archived_at")
