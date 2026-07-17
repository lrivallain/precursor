"""cockpit url kind

Revision ID: 20e5dfd1fffd
Revises: 7078d9d3d4d4
Create Date: 2026-07-17 17:41:28.371179

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20e5dfd1fffd"
down_revision: str | None = "7078d9d3d4d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only — plain ADD COLUMN, no table rebuild. (A batch rebuild here
    # tripped SQLAlchemy's column sorter with a CircularDependencyError, and
    # rebuilding a populated table on SQLite is needlessly risky.)
    #
    # Idempotent: SQLite DDL isn't transactional, so a previously failed run of
    # this migration can leave a column already added. Add each only if missing.
    # URL cockpits keep the NOT NULL command/port columns filled with harmless
    # defaults ("" / 0); the API presents them as null.
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("cockpits")}
    if "kind" not in existing:
        op.add_column(
            "cockpits",
            sa.Column("kind", sa.String(length=16), server_default="command", nullable=False),
        )
    if "url" not in existing:
        op.add_column("cockpits", sa.Column("url", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("cockpits")}
    if "url" in existing:
        op.drop_column("cockpits", "url")
    if "kind" in existing:
        op.drop_column("cockpits", "kind")
