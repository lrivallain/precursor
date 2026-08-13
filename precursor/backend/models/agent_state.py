"""AgentState — an agent's private, durable scratchpad across runs.

This is the third storage surface an agent can reach, and it exists because
neither of the other two fits *cross-run bookkeeping*:

* :class:`~precursor.backend.models.memory.Memory` is **global and always
  injected** into every turn of every chat. Putting a scheduled agent's cursor
  there would leak it into unrelated conversations and grow the prompt forever.
* :class:`~precursor.backend.models.agent_artifact.AgentArtifact` is a
  *published deliverable* on the fleet blackboard, and is deliberately **wiped
  at the start of every fresh run** (``AgentManager._clear_artifacts``) so a
  re-run's outputs replace the previous run's rather than piling up.

State is the opposite of both: **private to one agent, structured, and it
survives re-runs**. It's what a recurring agent (see ``AgentSchedule``) or a
webhook-triggered one (``AgentTrigger``) needs to answer "where did I get to
last time?" — a cursor, a set of already-seen ids, a counter, a digest of the
last payload it acted on.

The defining rule: **values are never auto-injected into the prompt.** Only the
*key index* is (see ``services/agent_state.build_state_index_prompt``), so the
agent knows what it can look up and pulls the body on demand through the
``state_get`` MCP tool. Injecting the values would recreate exactly the
context-bloat problem that makes ``Memory`` the wrong home for this.

Values are opaque to Precursor — conventionally a JSON document, but any text
is accepted; the agent owns the shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.agent_session import AgentSession

# Guardrails. Large blobs and binaries belong in a Workspace file with only a
# pointer (path + digest) stored here — the DB is for bookkeeping, not payloads.
AGENT_STATE_MAX_KEY = 120
AGENT_STATE_MAX_VALUE = 100_000
# Cap on distinct keys per agent, so a looping agent can't grow the table
# without bound. Writing beyond it is rejected rather than silently evicting:
# an agent that has lost track of its own keyspace should be told, not pruned.
AGENT_STATE_MAX_KEYS = 200


class AgentState(Base, TimestampMixin):
    __tablename__ = "agent_states"
    __table_args__ = (UniqueConstraint("agent_id", "key", name="uq_agent_state_agent_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Caller-chosen handle, unique per agent. Upserted on write.
    key: Mapped[str] = mapped_column(String(AGENT_STATE_MAX_KEY), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")

    agent: Mapped[AgentSession] = relationship("AgentSession", back_populates="states")
