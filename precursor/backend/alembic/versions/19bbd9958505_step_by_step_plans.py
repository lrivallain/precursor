"""step by step plans

Revision ID: 19bbd9958505
Revises: b7c4e91d2a58
Create Date: 2026-10-28 14:39:55.336664

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "19bbd9958505"
down_revision: str | None = "b7c4e91d2a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "step_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="drafting", nullable=False),
        sa.Column("source_topic_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("llm_selection", sa.Text(), nullable=True),
        sa.Column("output_instructions", sa.Text(), nullable=True),
        sa.Column("final_output", sa.Text(), nullable=True),
        sa.Column("final_stale", sa.Boolean(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["source_topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_step_plans_source_topic_id"), "step_plans", ["source_topic_id"], unique=False
    )
    op.create_table(
        "steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("parent_step_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("artifact", sa.Text(), nullable=False),
        sa.Column("artifact_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.String(length=8), server_default="user", nullable=False),
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
        sa.ForeignKeyConstraint(["parent_step_id"], ["steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["step_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_steps_parent_step_id"), "steps", ["parent_step_id"], unique=False)
    op.create_index(op.f("ix_steps_plan_id"), "steps", ["plan_id"], unique=False)
    op.create_table(
        "step_artifact_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=8), nullable=False),
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
        sa.ForeignKeyConstraint(["step_id"], ["steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_step_artifact_versions_step_id"),
        "step_artifact_versions",
        ["step_id"],
        unique=False,
    )
    # Plain columns without FK constraints, like the other chat link columns:
    # SQLite can't add a constraint in place and doesn't enforce it anyway.
    op.add_column("chats", sa.Column("step_id", sa.Integer(), nullable=True))
    op.add_column("chats", sa.Column("step_plan_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_chats_step_id"), "chats", ["step_id"], unique=False)
    op.create_index(op.f("ix_chats_step_plan_id"), "chats", ["step_plan_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chats_step_plan_id"), table_name="chats")
    op.drop_index(op.f("ix_chats_step_id"), table_name="chats")
    op.drop_column("chats", "step_plan_id")
    op.drop_column("chats", "step_id")
    op.drop_index(op.f("ix_step_artifact_versions_step_id"), table_name="step_artifact_versions")
    op.drop_table("step_artifact_versions")
    op.drop_index(op.f("ix_steps_plan_id"), table_name="steps")
    op.drop_index(op.f("ix_steps_parent_step_id"), table_name="steps")
    op.drop_table("steps")
    op.drop_index(op.f("ix_step_plans_source_topic_id"), table_name="step_plans")
    op.drop_table("step_plans")
