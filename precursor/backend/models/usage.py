"""UsageRecord model — a ledger of metered LLM round-trips.

Every billed call to a provider (a chat assistant turn, a tool round, or a
utility command like ``/notes`` or ``/gh-create``) records one row here. This
ledger — not the ``messages`` table — is the single source of truth for the
global usage statistics, so utility calls that never persist a conversation
message are still counted.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, TimestampMixin


class UsageRecord(Base, TimestampMixin):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Where the call originated: "chat" for conversation turns, or the command
    # label (e.g. "/notes rephrase", "/gh-create draft") for utility calls.
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="chat")

    # Model id used for the call, when known.
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Optional links to the originating container. Both nullable: utility calls
    # may have no container, and we keep the row even if the container is later
    # deleted (SET NULL) so historical totals stay intact.
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), index=True, nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), index=True, nullable=True
    )

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
