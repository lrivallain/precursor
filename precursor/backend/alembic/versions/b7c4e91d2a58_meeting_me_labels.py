"""meeting sessions: me_labels_json

Speaker labels the user marked as themselves, so suggested answers only react to
questions asked by someone else.

Revision ID: b7c4e91d2a58
Revises: f3a8d1c6b274
Create Date: 2026-10-15 17:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4e91d2a58"
down_revision: str | None = "f3a8d1c6b274"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meeting_sessions",
        sa.Column("me_labels_json", sa.Text(), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("meeting_sessions", "me_labels_json")
