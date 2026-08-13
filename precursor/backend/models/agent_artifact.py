"""AgentArtifact — an entry on the shared fleet blackboard.

Agents publish durable, named outputs here (a summary, a JSON result, a link,
a markdown doc) instead of leaving them buried in the transcript. Two things
consume artifacts:

* **downstream agents** — when a dependent agent is released by the fleet runner,
  its upstreams' artifacts are injected into its kickoff so results flow along
  the DAG (the "blackboard" pattern);
* **the operator** — the agent view lists artifacts so a mission's tangible
  outputs are one click away.

An agent publishes via the ``ARTIFACT: <title> | <content>`` directive (parsed
like ``PROGRESS:``) or the API; its final ``result_summary`` is also captured as
an artifact automatically on completion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.agent_session import AgentSession

# Rendering hint for the artifact body. Plain string (no migration to add kinds).
AGENT_ARTIFACT_KINDS = ("text", "markdown", "json", "link")


class AgentArtifact(Base, TimestampMixin):
    __tablename__ = "agent_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Optional stable handle for a well-known output (e.g. "result", "report").
    key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="text", server_default="text"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Artifact")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    agent: Mapped[AgentSession] = relationship("AgentSession", back_populates="artifacts")
