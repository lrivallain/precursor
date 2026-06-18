"""add chat support to attachments

Revision ID: 0008_chat_attachments
Revises: 0007_chat_support
Create Date: 2026-06-17

Lets image attachments belong to a flat ``Chat`` as well as a ``Topic``. Mirrors
the messages change in 0007: add a nullable ``chat_id`` foreign key, relax
``topic_id`` to nullable, and enforce that exactly one container is set.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0008_chat_attachments"
down_revision = "0007_chat_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {c["name"]: c for c in inspector.get_columns("attachments")}
    constraints = {c["name"] for c in inspector.get_check_constraints("attachments")}

    needs_chat_id = "chat_id" not in cols
    needs_nullable = cols.get("topic_id", {}).get("nullable") is False
    needs_constraint = "ck_attachment_container" not in constraints
    if not (needs_chat_id or needs_nullable or needs_constraint):
        return

    with op.batch_alter_table("attachments", recreate="always") as batch:
        if needs_nullable:
            batch.alter_column("topic_id", existing_type=sa.Integer(), nullable=True)
        if needs_chat_id:
            batch.add_column(
                sa.Column(
                    "chat_id",
                    sa.Integer,
                    sa.ForeignKey("chats.id", ondelete="CASCADE"),
                    nullable=True,
                )
            )
            batch.create_index(op.f("ix_attachments_chat_id"), ["chat_id"])
        if needs_constraint:
            batch.create_check_constraint(
                "ck_attachment_container",
                "(topic_id IS NOT NULL AND chat_id IS NULL) "
                "OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            )


def downgrade() -> None:
    # Complex container-relaxation migration; downgrade is not supported.
    pass
