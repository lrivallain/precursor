"""step plan final provisional

Revision ID: b87e1921ac3c
Revises: 19bbd9958505
Create Date: 2026-10-28 19:05:13.034255

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b87e1921ac3c"
down_revision: str | None = "19bbd9958505"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "step_plans",
        sa.Column("final_provisional", sa.Boolean(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("step_plans", "final_provisional")
