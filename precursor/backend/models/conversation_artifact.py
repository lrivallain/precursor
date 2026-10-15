"""Conversation artifact — a workspace file attached to a topic or a chat.

The link is many-to-many at the file level: a discussion can touch files in
several workspaces and a file can be discussed in several topics and chats. It
stores only ``(workspace, path)``; a file renamed or deleted outside the app
just reads as missing, nothing tracks it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.workspace import Workspace

# How the file became an artifact.
ARTIFACT_SOURCE_ASSISTANT = "assistant"  # the assistant's workspace write tools
ARTIFACT_SOURCE_MANUAL = "manual"  # attached by hand from the context area
ARTIFACT_SOURCE_OPENED = "opened"  # the discussion was opened on the file
ARTIFACT_SOURCES = (ARTIFACT_SOURCE_ASSISTANT, ARTIFACT_SOURCE_MANUAL, ARTIFACT_SOURCE_OPENED)


class ConversationArtifact(Base, TimestampMixin):
    __tablename__ = "conversation_artifacts"
    __table_args__ = (
        # Mirror Message/Attachment: exactly one container, enforced app-side too.
        CheckConstraint(
            "(topic_id IS NOT NULL AND chat_id IS NULL) OR (topic_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_conversation_artifact_container",
        ),
        # NULLs are distinct, so each constraint only bites for its own kind.
        UniqueConstraint("topic_id", "workspace_id", "path", name="uq_artifact_topic_file"),
        UniqueConstraint("chat_id", "workspace_id", "path", name="uq_artifact_chat_file"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=True
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Relative to the workspace's browse root, as in the file endpoints.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ARTIFACT_SOURCE_ASSISTANT, server_default="assistant"
    )

    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="artifacts")
