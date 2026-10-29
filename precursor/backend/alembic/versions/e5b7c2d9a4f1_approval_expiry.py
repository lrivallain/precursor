"""approval expiry for unattended runs

Adds ``tool_approvals.expires_at`` (when an approval parked by a scheduled run is
auto-denied) and ``topic_schedule.approval_timeout_minutes`` (a per-schedule
override of the global timeout).

Revision ID: e5b7c2d9a4f1
Revises: d2a96f4b1c58
Create Date: 2026-10-15 11:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b7c2d9a4f1"
down_revision: str | None = "d2a96f4b1c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tool_approvals") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_tool_approvals_expires_at", ["expires_at"])
    with op.batch_alter_table("topic_schedule") as batch:
        batch.add_column(sa.Column("approval_timeout_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("topic_schedule") as batch:
        batch.drop_column("approval_timeout_minutes")
    with op.batch_alter_table("tool_approvals") as batch:
        batch.drop_index("ix_tool_approvals_expires_at")
        batch.drop_column("expires_at")
