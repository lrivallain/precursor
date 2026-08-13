"""add per-agent approval policy override

Lets an individual agent session override the global ``agents_approval_policy``
gating its tool calls. ``NULL`` means inherit the global default, so a fleet can
keep one cautious default while a specific trusted mission runs more (or less)
autonomously. Values mirror the global setting: ``manual`` / ``balanced`` /
``autonomous``.

Revision ID: 2b3ae8016233
Revises: f2a3c4d5e6b7
Create Date: 2026-08-10 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b3ae8016233"
down_revision: str | None = "f2a3c4d5e6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("approval_policy", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "approval_policy")
