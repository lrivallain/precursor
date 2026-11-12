"""add Entra (Azure CLI) auth fields to user MCP servers

Lets a user-defined streamable-HTTP MCP server authenticate with an Entra
bearer token minted by the Azure CLI (``az account get-access-token``) instead
of a static header.

Revision ID: c5e2a7f19d40
Revises: 2c96c2451172
Create Date: 2026-11-12 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e2a7f19d40"
down_revision: str | None = "2c96c2451172"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("auth", sa.String(length=20), nullable=False, server_default="none"),
    )
    op.add_column("mcp_servers", sa.Column("auth_tenant", sa.String(length=255), nullable=True))
    op.add_column("mcp_servers", sa.Column("auth_resource", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch:
        batch.drop_column("auth_resource")
        batch.drop_column("auth_tenant")
        batch.drop_column("auth")
