"""definition file anchors and run provenance

Files mode (docs/definitions.md): anchors that tie a step's private agent and a
workflow step row to their place in a workflow file, and the definition file
(path + SHA-256) each agent run and workflow run executed.

Revision ID: 8e0ba6d4a757
Revises: f1e3c30b32f5
Create Date: 2026-09-26 12:52:08.679393

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e0ba6d4a757"
down_revision: str | None = "f1e3c30b32f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("definition_path", sa.String(length=500), nullable=True))
    op.add_column("agent_runs", sa.Column("definition_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "agent_sessions", sa.Column("definition_ref", sa.String(length=200), nullable=True)
    )
    op.create_index(
        op.f("ix_agent_sessions_definition_ref"), "agent_sessions", ["definition_ref"], unique=False
    )
    op.add_column(
        "workflow_runs", sa.Column("definition_path", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "workflow_runs", sa.Column("definition_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "workflow_steps", sa.Column("definition_ref", sa.String(length=200), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflow_steps", "definition_ref")
    op.drop_column("workflow_runs", "definition_hash")
    op.drop_column("workflow_runs", "definition_path")
    op.drop_index(op.f("ix_agent_sessions_definition_ref"), table_name="agent_sessions")
    op.drop_column("agent_sessions", "definition_ref")
    op.drop_column("agent_runs", "definition_hash")
    op.drop_column("agent_runs", "definition_path")
