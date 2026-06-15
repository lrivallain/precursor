"""add local workspaces (workspaces.kind + nullable repo_url)

Revision ID: 0005_local_workspaces
Revises: 0004_schedule_time_of_day
Create Date: 2026-06-14

Adds a ``kind`` discriminator ("git" | "local") to ``workspaces`` and relaxes
``repo_url`` to be nullable so local (non-git) folder workspaces can exist.
Guarded to be a no-op when already applied.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0005_local_workspaces"
down_revision = "0004_schedule_time_of_day"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "workspaces" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("workspaces")}
    if "kind" not in cols:
        op.add_column(
            "workspaces",
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="git"),
        )
    # Relax repo_url to nullable. batch_alter_table makes this work on SQLite
    # (table rebuild) as well as on databases with native ALTER support.
    with op.batch_alter_table("workspaces") as batch:
        batch.alter_column(
            "repo_url",
            existing_type=sa.String(length=1024),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "workspaces" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("workspaces")}
    if "kind" in cols:
        op.drop_column("workspaces", "kind")
