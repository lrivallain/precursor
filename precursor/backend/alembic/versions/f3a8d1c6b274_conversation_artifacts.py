"""conversation artifacts

Workspace files attached to a topic or chat (many to many), replacing the
workspace's own ephemeral chat. A workspace no longer carries an assistant
role, so ``workspaces.role`` goes. The legacy ``role_id`` column stays, as
``b5e19c7a3d42`` left it.

Revision ID: f3a8d1c6b274
Revises: e5b7c2d9a4f1
Create Date: 2026-10-15 10:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a8d1c6b274"
down_revision = "e5b7c2d9a4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="assistant", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) OR "
            "(topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_conversation_artifact_container",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "workspace_id", "path", name="uq_artifact_chat_file"),
        sa.UniqueConstraint("topic_id", "workspace_id", "path", name="uq_artifact_topic_file"),
    )
    op.create_index(
        op.f("ix_conversation_artifacts_chat_id"), "conversation_artifacts", ["chat_id"]
    )
    op.create_index(
        op.f("ix_conversation_artifacts_topic_id"), "conversation_artifacts", ["topic_id"]
    )
    op.create_index(
        op.f("ix_conversation_artifacts_workspace_id"), "conversation_artifacts", ["workspace_id"]
    )

    op.drop_index(op.f("ix_workspaces_role"), table_name="workspaces")
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.drop_column("role")


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.add_column(sa.Column("role", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_workspaces_role"), "workspaces", ["role"])

    op.drop_index(
        op.f("ix_conversation_artifacts_workspace_id"), table_name="conversation_artifacts"
    )
    op.drop_index(op.f("ix_conversation_artifacts_topic_id"), table_name="conversation_artifacts")
    op.drop_index(op.f("ix_conversation_artifacts_chat_id"), table_name="conversation_artifacts")
    op.drop_table("conversation_artifacts")
