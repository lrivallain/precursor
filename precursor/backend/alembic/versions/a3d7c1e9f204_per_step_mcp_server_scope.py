"""per-step MCP server scope

Revision ID: a3d7c1e9f204
Revises: c8f1e60a3b74
Create Date: 2026-08-13 14:00:00.000000

A workflow step's only tool control until now was the all-or-nothing
``use_mcp``: every enabled MCP server, or none. The tool catalogue is by far the
largest thing in a step's context — on an instrumented six-step run, the
tool-enabled steps cost ~110K input tokens per round-trip against ~17K for the
tool-free ones, and each of them actually needed a single server — so "all" is
both expensive and unfocused, and asking a step in its prompt not to use a tool
it can see does not hold.

Two nullable columns land together because they are the two ends of one channel:

* ``workflow_steps.mcp_servers`` — what the *author* declares, comma-separated
  server names. Null keeps today's behaviour (every enabled server).
* ``agent_sessions.mcp_servers`` — where the workflow engine writes the running
  step's scope, exactly as it already does for ``use_mcp``, because the agent
  runtime is handed the agent row and never sees the step.

Both are nullable with no backfill: null *is* the pre-existing behaviour.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3d7c1e9f204"
down_revision = "c8f1e60a3b74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_steps", sa.Column("mcp_servers", sa.String(length=400), nullable=True))
    op.add_column("agent_sessions", sa.Column("mcp_servers", sa.String(length=400), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "mcp_servers")
    op.drop_column("workflow_steps", "mcp_servers")
