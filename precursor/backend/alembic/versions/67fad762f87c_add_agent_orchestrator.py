"""add agent orchestrator (deps, triggers, blueprints, artifacts, governance)

Turns the agents feature into a multi-agent orchestrator. Adds:

* governance/budget + retry + provenance columns on ``agent_sessions``
  (token_budget, total_input_tokens, total_output_tokens, max_retries,
  retry_count, next_retry_at, blueprint_id, finished_at);
* ``agent_blueprints`` — reusable agent templates;
* ``agent_deps`` — the fleet DAG (dependent -> upstream edges);
* ``agent_triggers`` — external webhook starts;
* ``agent_artifacts`` — the shared blackboard of published outputs.

Revision ID: 67fad762f87c
Revises: 2b3ae8016233
Create Date: 2026-08-11 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "67fad762f87c"
down_revision: str | None = "2b3ae8016233"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Blueprints (created first: agent_sessions.blueprint_id FKs it) ------
    op.create_table(
        "agent_blueprints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("approval_policy", sa.String(length=16), nullable=True),
        sa.Column("autonomy_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("token_budget", sa.Integer(), nullable=True),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("icon", sa.String(length=40), nullable=True),
        sa.Column("color", sa.String(length=24), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_blueprints_role_id", "agent_blueprints", ["role_id"])

    # --- New columns on agent_sessions --------------------------------------
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(sa.Column("token_budget", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("blueprint_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_agent_sessions_blueprint_id",
            "agent_blueprints",
            ["blueprint_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_agent_sessions_next_retry_at", "agent_sessions", ["next_retry_at"])
    op.create_index("ix_agent_sessions_blueprint_id", "agent_sessions", ["blueprint_id"])

    # --- Dependencies (the DAG) ---------------------------------------------
    op.create_table(
        "agent_deps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "depends_on_id", name="uq_agent_dep_edge"),
    )
    op.create_index("ix_agent_deps_agent_id", "agent_deps", ["agent_id"])
    op.create_index("ix_agent_deps_depends_on_id", "agent_deps", ["depends_on_id"])

    # --- Triggers (webhooks) -------------------------------------------------
    op.create_table(
        "agent_triggers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False, server_default="webhook"),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_triggers_agent_id", "agent_triggers", ["agent_id"])
    op.create_index("ix_agent_triggers_token", "agent_triggers", ["token"], unique=True)

    # --- Artifacts (blackboard) ---------------------------------------------
    op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="text"),
        sa.Column("title", sa.String(length=200), nullable=False, server_default="Artifact"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_artifacts_agent_id", "agent_artifacts", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_artifacts_agent_id", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")

    op.drop_index("ix_agent_triggers_token", table_name="agent_triggers")
    op.drop_index("ix_agent_triggers_agent_id", table_name="agent_triggers")
    op.drop_table("agent_triggers")

    op.drop_index("ix_agent_deps_depends_on_id", table_name="agent_deps")
    op.drop_index("ix_agent_deps_agent_id", table_name="agent_deps")
    op.drop_table("agent_deps")

    op.drop_index("ix_agent_sessions_blueprint_id", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_next_retry_at", table_name="agent_sessions")
    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_constraint("fk_agent_sessions_blueprint_id", type_="foreignkey")
        batch.drop_column("finished_at")
        batch.drop_column("blueprint_id")
        batch.drop_column("next_retry_at")
        batch.drop_column("retry_count")
        batch.drop_column("max_retries")
        batch.drop_column("total_output_tokens")
        batch.drop_column("total_input_tokens")
        batch.drop_column("token_budget")

    op.drop_index("ix_agent_blueprints_role_id", table_name="agent_blueprints")
    op.drop_table("agent_blueprints")
