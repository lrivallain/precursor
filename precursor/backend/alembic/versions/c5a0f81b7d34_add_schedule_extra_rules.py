"""add extra_rules to schedules and workflows

Lets one schedulable item carry several recurrence rules at once — e.g. "every
day at 07:00" *plus* "every weekday at 12:00". The first rule stays in the
existing ``interval_seconds`` / ``run_at_minute`` / ``days_of_week`` /
``timezone`` columns so every existing row keeps its meaning; the additional
rules are JSON-encoded into this new column. ``next_run_at`` is materialised as
the earliest of them, so the scheduler's polling query is unchanged.

Purely additive and nullable: existing single-rule schedules read back as a
one-element rule list.

Revision ID: c5a0f81b7d34
Revises: 175c536abb15
Create Date: 2026-09-03 10:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5a0f81b7d34"
down_revision: str | None = "175c536abb15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("topic_schedule", "agent_schedule", "workflows")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("extra_rules", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in reversed(_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("extra_rules")
