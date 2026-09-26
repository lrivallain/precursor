"""definition snapshot on workflow runs

Files mode (docs/definitions.md): the text of the workflow file a run started
from, so the run keeps executing that version — also after a restart.

Revision ID: 5d2e8f41a7b3
Revises: 3c7a91d2e5f4
Create Date: 2026-09-26 16:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d2e8f41a7b3"
down_revision: str | None = "3c7a91d2e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("definition_snapshot", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_runs", "definition_snapshot")
