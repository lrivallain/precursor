"""ToolApproval model — a tool call parked until the user decides on it."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin

APPROVAL_KIND_TOOL = "tool"
APPROVAL_KIND_INTENT = "intent"
APPROVAL_KINDS = (APPROVAL_KIND_TOOL, APPROVAL_KIND_INTENT)

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_DENIED = "denied"
APPROVAL_CANCELLED = "cancelled"
APPROVAL_STATUSES = (APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_DENIED, APPROVAL_CANCELLED)

APPROVAL_SCOPE_ONCE = "once"
APPROVAL_SCOPE_CONVERSATION = "conversation"
APPROVAL_SCOPES = (APPROVAL_SCOPE_ONCE, APPROVAL_SCOPE_CONVERSATION)


class ToolApproval(Base, TimestampMixin):
    """One tool call (or model-declared intent) awaiting a user decision.

    Persisted so a pending approval survives a reload: the turn that raised it
    is parked (its assistant tool-call row is saved, the tool results are not)
    and resumes once every approval in the conversation is decided and executed.
    """

    __tablename__ = "tool_approvals"
    __table_args__ = (
        CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_tool_approval_container",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # The assistant row that carries the tool call; its tool rows follow it.
    assistant_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Qualified ``server__tool`` name as the model saw it.
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    server: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    raw_tool: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    arguments: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # ``tool``: the policy gated a real call. ``intent``: the model asked first.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=APPROVAL_KIND_TOOL)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=APPROVAL_PENDING, index=True
    )
    # ``conversation``: later calls to the same tool in this conversation skip the prompt.
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default=APPROVAL_SCOPE_ONCE)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # True once the tool result row for this call has been written.
    executed: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="0")
