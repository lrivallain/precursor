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

Artifacts are **run-scoped**: each belongs to the ``AgentRun`` that produced it.
Launching a run clears only that run's artifacts, so two workflows driving the
same agent no longer wipe each other's blackboard mid-flight. ``agent_id`` is
kept alongside for agent-wide listing and cheap cascade deletes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.agent_run import AgentRun
    from precursor.backend.models.agent_session import AgentSession

# Rendering hint for the artifact body. Plain string (no migration to add kinds).
AGENT_ARTIFACT_KINDS = ("text", "markdown", "json", "link")


class AgentArtifact(Base, TimestampMixin):
    __tablename__ = "agent_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The execution that published this. Nullable only for rows migrated from
    # before runs existed and for artifacts attached to an agent that has never
    # run; every new artifact carries one.
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Optional stable handle for a well-known output (e.g. "result", "report").
    key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="text", server_default="text"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Artifact")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    agent: Mapped[AgentSession] = relationship(
        "AgentSession", back_populates="artifacts", foreign_keys=[agent_id]
    )
    run: Mapped[AgentRun | None] = relationship(
        "AgentRun", back_populates="artifacts", foreign_keys=[agent_run_id]
    )
