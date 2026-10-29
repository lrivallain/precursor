"""definition trust: accepted permissions

Files mode (docs/definitions.md): the permission snapshot a human last accepted
for an agent's or workflow's definition file, so a file that widens it is held
for review before it can run.

Revision ID: 3c7a91d2e5f4
Revises: 8e0ba6d4a757
Create Date: 2026-09-26 14:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c7a91d2e5f4"
down_revision: str | None = "8e0ba6d4a757"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("accepted_permissions", sa.Text(), nullable=True))
    op.add_column("workflows", sa.Column("accepted_permissions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflows", "accepted_permissions")
    op.drop_column("agent_sessions", "accepted_permissions")
