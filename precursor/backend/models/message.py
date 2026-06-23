"""Message model — a single turn in a topic or chat conversation."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.attachment import Attachment
    from precursor.backend.models.chat import Chat
    from precursor.backend.models.topic import Topic


class MessageRole(str, enum.Enum):  # noqa: UP042 - StrEnum would change str() semantics relied on elsewhere
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (
        # Ensure exactly one of topic_id or chat_id is set (app-layer enforces this too).
        CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_message_container",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=True
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Set when this message was posted into the container by an Agents-mode
    # session (the prompt + answer of an agent exchange). Lets the UI render an
    # "agent exchange" badge with a deep link back to /agents/{id}. SET NULL on
    # agent deletion so the exchange text survives but the link disappears.
    agent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"), index=True, nullable=True
    )

    # Optional serialized tool-call payload (JSON string) for assistant turns.
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Token usage reported by the provider for the round-trip that produced
    # this assistant message (NULL for user/tool/system turns and for runs
    # against providers that don't surface usage).
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    topic: Mapped[Topic | None] = relationship("Topic", back_populates="messages")
    chat: Mapped[Chat | None] = relationship("Chat", back_populates="messages")
    attachments: Mapped[list[Attachment]] = relationship(
        "Attachment",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="Attachment.id",
    )
