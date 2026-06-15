"""rename knowledge_areas to workspaces

Revision ID: 0001_workspaces
Revises:
Create Date: 2026-06-14

Renames the ``knowledge_areas`` table to ``workspaces``. Guarded so it is a
no-op when the old table is absent (fresh databases are created directly with
the new name via ``create_all``).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0001_workspaces"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "knowledge_areas" in tables and "workspaces" not in tables:
        op.rename_table("knowledge_areas", "workspaces")


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "workspaces" in tables and "knowledge_areas" not in tables:
        op.rename_table("workspaces", "knowledge_areas")
