"""add description_as_system_prompt to chats and merge migration heads

Revision ID: 0010_chat_description_as_system_prompt
Revises: 0009_reminders, 0009_usage_records
Create Date: 2026-06-19

Adds the ``description_as_system_prompt`` flag to ``chats`` so a chat's
description can be enforced as a per-turn system instruction instead of soft
discussion-level context. Also merges the two ``0009`` heads (reminders and
usage_records) into a single linear head. Guarded to be a no-op when already
applied.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0010_chat_description_as_system_prompt"
down_revision = ("0009_reminders", "0009_usage_records")
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "chats" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("chats")}
    if "description_as_system_prompt" not in cols:
        op.add_column(
            "chats",
            sa.Column(
                "description_as_system_prompt",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "chats" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("chats")}
    if "description_as_system_prompt" in cols:
        op.drop_column("chats", "description_as_system_prompt")
