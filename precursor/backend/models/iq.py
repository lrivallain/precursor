"""Precursor IQ retrieval index.

``IQChunk`` rows are a derived, rebuildable copy of searchable content split
into passages: a full-text index sits on top of them (an FTS5 virtual table on
SQLite, a generated ``tsvector`` column on Postgres — both created by the
migration, not mapped here) and, when embeddings are on, a vector per chunk.

``IQDirty`` is the work queue: writes to an indexed model enqueue their
``(source_kind, source_id)`` in the same transaction (see ``services/iq/events``)
and the indexer drains it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from precursor.backend.models.base import Base, _utcnow


class IQChunk(Base):
    __tablename__ = "iq_chunks"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", "chunk_no", name="uq_iq_chunks_source"),
        Index("ix_iq_chunks_container", "container_kind", "container_id"),
        Index("ix_iq_chunks_embedding_model", "embedding_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # What produced the chunk (topic, message, live_transcript, …) and its row id.
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The MCP exposure section that gates this chunk for external callers.
    section: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # The navigable entity the chunk belongs to (topic/chat/agent/live/memory).
    container_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    container_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which field of the source the text came from (title, message, brief, …).
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Indexed heading, weighted above the body. Only the entity's own chunk
    # carries its title, so a title hit doesn't lift every message beneath it.
    heading: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Normalised float32 vector; null until the embeddings pass reaches it.
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # ``<provider>:<model>`` the vector came from, so switching either
    # invalidates it instead of comparing vectors from different spaces.
    embedding_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class IQDirty(Base):
    __tablename__ = "iq_dirty"

    source_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
