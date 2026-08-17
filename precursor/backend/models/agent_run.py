"""AgentRun — one execution of an agent.

``AgentSession`` used to be two things at once: the *definition* of an agent
(title, objective, model, capability defaults) **and** the state of whatever was
currently driving it (status, active prompt, token counters, the SDK handle).
That conflation held only while an agent could have at most one execution in
flight. The moment two workflows point a step at the same agent and both run,
they overwrite each other's status, inherit each other's live SDK session, wipe
each other's artifacts and mis-attribute each other's tokens.

This row is that missing half. ``AgentSession`` is now the reusable definition;
``AgentRun`` is a single execution of it — mirroring ``Workflow``/``WorkflowRun``.
Every runtime registry, the live SDK session, artifacts, events and the workflow
advance seam key off ``AgentRun.id``, so two executions of the same agent are
fully isolated.

Runs are append-only history: they are never deleted when they finish, so the
trace of what an agent actually did survives (mirroring ``WorkflowRunStep``).

The execution columns still present on ``AgentSession`` are a denormalised
*mirror* of the agent's most recent run, kept so the Agents list, inbox, metrics
and fleet governor can read status without joining. This row is authoritative;
the mirror is a cache (see ``_mirror_run_to_agent``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.agent_artifact import AgentArtifact
    from precursor.backend.models.agent_session import AgentSession

# What kicked this execution off. Mirrors ``WORKFLOW_RUN_TRIGGERS`` in spirit but
# covers the agent-level entry points too. Plain string — adding one needs no
# migration.
AGENT_RUN_TRIGGERS = (
    "manual",  # a human started/restarted the agent from the cockpit
    "workflow",  # a workflow step launched it
    "schedule",  # an AgentSchedule cadence fired
    "webhook",  # an AgentTrigger fired
    "fleet",  # the fleet governor released it from ``pending``
    "retry",  # auto-retry after a failed turn, or a workflow step retry
    "replay",  # an operator re-ran a single workflow step out of band
)


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (
        # The Agents cockpit and the run-resolution path both want "this agent's
        # latest run" — a covering index keeps that a single seek.
        Index("ix_agent_runs_agent_created", "agent_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- Provenance ----------------------------------------------------------
    # Set when a workflow step drove this execution. Null for manual runs,
    # schedules, webhooks and fleet releases — those are agent-level executions
    # with no owning pipeline.
    workflow_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The specific trace row this run is the execution of. SET NULL so a pruned
    # trace doesn't take the run's own history with it.
    workflow_run_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_run_steps.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default="manual"
    )

    # The SDK session id for **this execution**. Previously lived on
    # ``AgentSession``, which is exactly why two concurrent workflows shared one
    # live session. Handed to ``create_session(session_id=...)``, which the SDK
    # adopts as its own id.
    #
    # Deliberately indexed but **not** unique: ``clear_session(keep_id=True)``
    # starts a fresh run that inherits the previous run's handle so a manual
    # agent keeps its conversational context. The real invariant — at most one
    # *non-terminal* run may hold a given handle — is enforced in the service
    # layer, since it is a liveness rule rather than a storage one.
    copilot_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # --- Execution state (moved off AgentSession) ----------------------------
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True
    )
    # The prompt for the turn currently in flight: set when a task/follow-up is
    # sent, cleared once the turn finishes and is posted back. Survives a restart
    # so an interrupted turn can be re-sent on resume and still notify back.
    active_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When ``status == "blocked"``, the decision the agent raised for a human
    # (via a ``NEED_INPUT:`` directive).
    blocked_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Autonomous steps taken toward the objective in *this* execution. Guards the
    # goal loop against runaway continuation; the budget itself (``max_steps``)
    # stays on the definition.
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Stall detection: how many consecutive turns produced no new progress, and
    # the last progress marker seen. Previously in-memory on ``_LiveSession``
    # only; persisted per run so a restart doesn't reset a stalling agent's count.
    stall_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_progress: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Token spend for **this execution**. The workflow trace's per-attempt delta
    # is computed against these, so interleaved runs of one agent no longer
    # attribute each other's usage. The cumulative cap (``token_budget``) stays
    # on the definition — it governs the agent, not one run.
    total_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # --- Capability snapshot -------------------------------------------------
    # Frozen for the life of the run. A workflow step used to write these onto
    # the shared agent row as it launched — so a second workflow starting a step
    # silently re-scoped the first one's tools mid-flight. Snapshotting them here
    # also means editing the agent definition while a run is in flight no longer
    # changes what that run is allowed to do.
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    use_mcp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    use_skills: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    use_memory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Comma-separated server names narrowing ``use_mcp``; null = every enabled
    # server. Matches ``AgentSession.mcp_servers``.
    mcp_servers: Mapped[str | None] = mapped_column(String(400), nullable=True)
    approval_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )

    # --- Timestamps ----------------------------------------------------------
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    agent: Mapped[AgentSession] = relationship(
        "AgentSession", back_populates="runs", foreign_keys=[agent_id]
    )
    # Outputs published during this execution. Run-scoped so a concurrent run of
    # the same agent can no longer wipe them.
    artifacts: Mapped[list[AgentArtifact]] = relationship(
        "AgentArtifact",
        back_populates="run",
        order_by="AgentArtifact.created_at.desc()",
    )
