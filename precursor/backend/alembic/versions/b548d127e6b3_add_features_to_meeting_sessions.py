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


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Idempotent: SQLite treats DDL as non-transactional, so a crash between the
    # ADD COLUMN and the version stamp (e.g. two dev servers racing on one DB)
    # can leave the column present but the revision unstamped. Guard so a re-run
    # doesn't die on "duplicate column".
    if not _has_column("meeting_sessions", "features_json"):
        op.add_column(
            "meeting_sessions",
            sa.Column(
                "features_json", sa.Text(), server_default='["insights", "notes"]', nullable=False
            ),
        )


def downgrade() -> None:
    if _has_column("meeting_sessions", "features_json"):
        op.drop_column("meeting_sessions", "features_json")
