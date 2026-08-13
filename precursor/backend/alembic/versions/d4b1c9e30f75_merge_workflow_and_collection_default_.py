"""merge workflow and collection default-role heads

Revision ID: d4b1c9e30f75
Revises: b2c3d4e5f6a7, b6e1f70a3c48
Create Date: 2026-08-13 09:04:13.755643

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

revision: str = "d4b1c9e30f75"
down_revision: str | Sequence[str] | None = ("b2c3d4e5f6a7", "b6e1f70a3c48")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
