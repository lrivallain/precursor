"""preserve approval source turns

Revision ID: 2c96c2451172
Revises: e83eedc78f50
Create Date: 2026-11-05 18:29:45.445376
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2c96c2451172"
down_revision: str | None = "e83eedc78f50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tool_approvals") as batch:
        batch.add_column(sa.Column("source_message_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tool_approval_source_message",
            "messages",
            ["source_message_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("tool_approvals") as batch:
        batch.drop_constraint("fk_tool_approval_source_message", type_="foreignkey")
        batch.drop_column("source_message_id")
