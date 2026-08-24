"""drop the github_models provider

The GitHub Models service was shut down by GitHub and the provider is gone from
the registry. An install still pointed at it would otherwise resolve to no spec
at all and silently fall back to the mock provider — fake replies with no
explanation — so repoint it at GitHub Copilot, which authenticates with the very
same GitHub token.

Revision ID: e3b7a1c95d24
Revises: e7c4a91d3b60
Create Date: 2026-08-24 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "e3b7a1c95d24"
down_revision = "e7c4a91d3b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Values in app_settings are JSON-encoded, so the string carries its quotes.
    op.execute(
        """
        UPDATE app_settings
           SET value = '"github_copilot"'
         WHERE key = 'llm_provider'
           AND value = '"github_models"'
        """
    )


def downgrade() -> None:
    # Not reversible: the pre-migration value is indistinguishable from an
    # install that chose GitHub Copilot on its own.
    pass
