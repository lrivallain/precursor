"""ApprovalRule model — a standing answer to "may the model call this tool?"."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin

RULE_ALLOW = "allow"
RULE_ASK = "ask"
RULE_EFFECTS = (RULE_ALLOW, RULE_ASK)

RULE_SCOPE_GLOBAL = "global"
RULE_SCOPE_TOPIC = "topic"
RULE_SCOPE_CHAT = "chat"
RULE_SCOPES = (RULE_SCOPE_GLOBAL, RULE_SCOPE_TOPIC, RULE_SCOPE_CHAT)


class ApprovalRule(Base, TimestampMixin):
    """Lets a matching tool call run (``allow``) or forces it to wait (``ask``).

    Rules are exceptions to the conversation's approval policy: they are looked
    at first, and a call no rule matches falls back to the policy. ``server`` and
    ``tool`` are globs (``*`` matches anything); ``arg_match`` narrows the rule
    to calls whose arguments satisfy every clause.
    """

    __tablename__ = "approval_rules"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'global' AND topic_id IS NULL AND chat_id IS NULL)"
            " OR (scope = 'topic' AND topic_id IS NOT NULL AND chat_id IS NULL)"
            " OR (scope = 'chat' AND chat_id IS NOT NULL AND topic_id IS NULL)",
            name="ck_approval_rule_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    effect: Mapped[str] = mapped_column(String(8), nullable=False)
    server: Mapped[str] = mapped_column(String(128), nullable=False, default="*")
    tool: Mapped[str] = mapped_column(String(255), nullable=False, default="*")
    # JSON list of ``{"arg": str, "op": "equals"|"glob"|"under", "value": str}``.
    arg_match: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    scope: Mapped[str] = mapped_column(String(8), nullable=False, default=RULE_SCOPE_GLOBAL)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The approval card this rule was created from; a plain id, not a foreign
    # key, so clearing a conversation's approvals never drops its rules.
    created_from_approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
