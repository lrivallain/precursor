"""roles referenced by name

A role is now looked up by its (unique) name, so it can live in a definition
file as well as in the ``roles`` table. Every table that pointed at ``roles.id``
gains a name column, backfilled from the row it pointed at.

The old ``role_id`` columns are left in place, unused: dropping a column that
carries a foreign key is a table rebuild on SQLite, which isn't worth the risk
until the ``roles`` table itself goes.

Revision ID: b5e19c7a3d42
Revises: a7d2c91e4b35
Create Date: 2026-10-06 09:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b5e19c7a3d42"
down_revision = "a7d2c91e4b35"
branch_labels = None
depends_on = None

# (table, old column, new column)
_COLUMNS = [
    ("topics", "role_id", "role"),
    ("chats", "role_id", "role"),
    ("workspaces", "role_id", "role"),
    ("meeting_sessions", "role_id", "role"),
    ("agent_sessions", "role_id", "role"),
    ("agent_runs", "role_id", "role"),
    ("agent_blueprints", "role_id", "role"),
    ("workflows", "role_id", "role"),
    ("collections", "default_role_id", "default_role"),
]


def upgrade() -> None:
    for table, old, new in _COLUMNS:
        op.add_column(table, sa.Column(new, sa.String(length=64), nullable=True))
        op.execute(
            f"UPDATE {table} SET {new} = "
            f"(SELECT roles.name FROM roles WHERE roles.id = {table}.{old}) "
            f"WHERE {old} IS NOT NULL"
        )
        if table != "agent_runs" and table != "agent_blueprints":
            op.create_index(f"ix_{table}_{new}", table, [new])


def downgrade() -> None:
    for table, _old, new in reversed(_COLUMNS):
        if table != "agent_runs" and table != "agent_blueprints":
            op.drop_index(f"ix_{table}_{new}", table_name=table)
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column(new)
