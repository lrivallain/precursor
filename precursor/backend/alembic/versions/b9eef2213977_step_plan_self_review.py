"""step plan self review

Revision ID: b9eef2213977
Revises: 5fa65fad981a
Create Date: 2026-11-05 09:18:17.780208

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9eef2213977"
down_revision: str | None = "5fa65fad981a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("step_plans") as batch:
        batch.add_column(sa.Column("self_review", sa.Boolean(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("step_plans") as batch:
        batch.drop_column("self_review")
