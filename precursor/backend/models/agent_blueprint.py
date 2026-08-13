"""AgentBlueprint — a reusable template for spawning agents.

A blueprint captures everything needed to stamp out an agent — its objective
(``task_prompt``), model, persona (``role_id``), approval policy, autonomy budget
and governance limits — so common missions become a one-click catalog instead of
being retyped each time. Instantiating a blueprint creates a fresh
``AgentSession`` seeded from these fields (and records ``blueprint_id`` on it for
provenance). Editing a blueprint never touches already-spawned agents.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class AgentBlueprint(Base, TimestampMixin):
    __tablename__ = "agent_blueprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The objective every agent spawned from this blueprint starts with.
    task_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")

    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Same override semantics as AgentSession: null = inherit global default.
    approval_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    autonomy_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=12, server_default="12")
    # Governance defaults stamped onto spawned agents (null token_budget = none).
    token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Presentation for the blueprint catalog card.
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(24), nullable=True)
