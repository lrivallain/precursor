"""add features to meeting_sessions

Revision ID: b548d127e6b3
Revises: 4083e9c16be8
Create Date: 2026-07-12 15:43:13.934219

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b548d127e6b3"
down_revision: str | None = "4083e9c16be8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meeting_sessions",
        sa.Column(
            "features_json", sa.Text(), server_default='["insights", "notes"]', nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("meeting_sessions", "features_json")
