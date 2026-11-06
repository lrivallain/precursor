"""Step plan amendments and chat sources.

Revision ID: 0e4480c7862e
Revises: b9eef2213977
Create Date: 2026-11-05 17:43:18.075534
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0e4480c7862e"
down_revision: str | None = "b9eef2213977"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("step_plans") as batch:
        batch.add_column(sa.Column("source_chat_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("amendments_json", sa.Text(), server_default="[]", nullable=False)
        )
        batch.create_index("ix_step_plans_source_chat_id", ["source_chat_id"])
        batch.create_foreign_key(
            "fk_step_plan_source_chat",
            "chats",
            ["source_chat_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("step_plans") as batch:
        batch.drop_constraint("fk_step_plan_source_chat", type_="foreignkey")
        batch.drop_index("ix_step_plans_source_chat_id")
        batch.drop_column("amendments_json")
        batch.drop_column("source_chat_id")
