"""add roles table, role_id FKs, and the default role

Revision ID: 0011_roles
Revises: 0010_chat_description_as_system_prompt
Create Date: 2026-06-19

Backfills the Assistant Roles feature into the migration chain (it had only
ever been applied via the old dev ``create_all`` + backfill path). Adds the
``roles`` table, the seeded protected ``default`` role, and a nullable
``role_id`` on each discussion container (topics, chats, workspaces).

Fully guarded / idempotent: every step is a no-op when the object already
exists, so it does nothing on a database that already has these (e.g. one built
by the legacy ``create_all`` path).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0011_roles"
down_revision = "0010_chat_description_as_system_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "roles" not in inspector.get_table_names():
        op.create_table(
            "roles",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("system_prompt", sa.Text, nullable=False, server_default=""),
            sa.Column("is_default", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)

    # Seed the protected default role (empty prompt) if absent.
    has_default = bind.execute(sa.text("SELECT 1 FROM roles WHERE is_default = 1 LIMIT 1")).first()
    if not has_default:
        bind.execute(
            sa.text(
                "INSERT INTO roles (name, system_prompt, is_default, created_at, updated_at) "
                "VALUES ('default', '', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    # Nullable role_id on each discussion container. A plain nullable column is
    # an in-place ALTER on SQLite (no table rebuild). SQLite doesn't enforce the
    # FK on an added column, which matches how the column was added before.
    for table in ("topics", "chats", "workspaces"):
        if table not in inspector.get_table_names():
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if "role_id" not in cols:
            op.add_column(table, sa.Column("role_id", sa.Integer, nullable=True))
            op.create_index(op.f(f"ix_{table}_role_id"), table, ["role_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("topics", "chats", "workspaces"):
        if table in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns(table)}
            if "role_id" in cols:
                op.drop_column(table, "role_id")
    if "roles" in inspector.get_table_names():
        op.drop_table("roles")
