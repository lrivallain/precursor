"""AgentTrigger — an external event that starts (or re-runs) an agent.

Where ``AgentSchedule`` fires on a clock, a trigger fires on an *external*
signal. The first supported type is ``webhook``: an unauthenticated-by-token
endpoint
(``POST /api/agents/hooks/{token}``) that kicks the agent, optionally overriding
its prompt from the request body. The random ``token`` is the only credential,
mirroring how GitHub/CI webhooks are addressed.

Keeping triggers on their own row (rather than columns on the agent) lets one
agent carry several and lets us add trigger types later without a schema churn.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.agent_session import AgentSession


# Trigger kinds. Only "webhook" for now; the column is a plain string so new
# kinds (e.g. file-watch, inbound-email) don't need a migration.
AGENT_TRIGGER_TYPES = ("webhook",)


def _mint_token() -> str:
    return secrets.token_urlsafe(24)


class AgentTrigger(Base, TimestampMixin):
    __tablename__ = "agent_triggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(24), nullable=False, default="webhook")
    # The URL-safe secret slug that addresses this trigger's webhook. Unique so a
    # POST to /hooks/{token} resolves to exactly one agent.
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, default=_mint_token
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    # JSON-encoded per-type options (reserved; e.g. {"clear_context": true}).
    config: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[AgentSession] = relationship("AgentSession", back_populates="triggers")
