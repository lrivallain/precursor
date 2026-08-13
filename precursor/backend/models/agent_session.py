"""AgentSession model — a long-running Copilot SDK agent task.

Agents mode runs deferred, autonomous work through the GitHub Copilot SDK, which
owns the agent loop **and** the durable session state on disk (keyed by
``copilot_session_id`` under the app's ``agents_home``). So Precursor keeps only
a thin pointer row here: the resume handle, the optional container it's linked
to (a topic *or* a chat, mirroring ``Message``/``Reminder``), and a denormalised
``status`` cache so the Agents tab can list sessions without booting the runtime.

The conversation/event history is **not** stored on this row. The SDK owns the
live session, but because ``session.get_events`` only replays ``SessionStartData``
on resume, the normalised workflow timeline is archived in the ``agent_events``
table (see ``AgentEventRecord``) so it survives restarts and session teardown.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from precursor.backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from precursor.backend.models.agent_artifact import AgentArtifact
    from precursor.backend.models.agent_schedule import AgentSchedule
    from precursor.backend.models.agent_trigger import AgentTrigger
    from precursor.backend.models.chat import Chat
    from precursor.backend.models.topic import Topic


# Lifecycle of an agent session. Kept in sync from the SDK event stream.
AGENT_STATUSES = (
    "pending",  # row created, runtime session not started yet
    "running",  # actively processing a turn
    "idle",  # finished a turn, waiting for follow-up input
    "needs_approval",  # blocked on a tool-permission request (SDK gate)
    "blocked",  # agent *raised* a question and parked itself for a human decision
    "completed",  # task finished (terminal — the agent declared its objective met)
    "failed",  # errored out
    "cancelled",  # aborted by the user
    "interrupted",  # process died mid-turn; resumable
)


class AgentSession(Base, TimestampMixin):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        # At most one container — same invariant as Message/Reminder. Both NULL
        # is allowed: an agent can run unlinked and be attached later.
        CheckConstraint(
            "NOT (topic_id IS NOT NULL AND chat_id IS NOT NULL)",
            name="ck_agent_session_container",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The SDK session id — the durable resume handle and the *public* identifier
    # used in deep links and the ``/agent`` command. We mint it ourselves (a
    # UUID) at row creation and hand it to ``create_session(session_id=...)``,
    # which the SDK adopts as its own session id. Generating it eagerly (rather
    # than waiting for the runtime to assign one) means every session has a
    # stable, shareable id from the moment it exists — even while ``pending`` or
    # if it never connects. Legacy rows created before this may still be null
    # until the runtime backfills one on first connect.
    copilot_session_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Agent task")
    # The initial instruction the agent was started with. This doubles as the
    # durable **objective** for autonomous runs: it's kept for display, for
    # restarting an interrupted session, and as the goal the goal-loop pursues
    # (the transient per-turn prompt lives in ``active_prompt``).
    task_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # --- Autonomy / mission state -------------------------------------------
    # When set, the agent runs a goal loop: after each turn it keeps taking the
    # next step toward ``task_prompt`` (its objective) on its own, pulling the
    # human in only by exception (a raised question, budget exhaustion, a stall).
    # Off by default — a plain agent stays a single-turn responder.
    autonomy_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Budget: the most autonomous continuation steps the goal loop will take
    # before it pauses and hands back to the human. Reset per fresh human intent.
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=12, server_default="12")
    # Autonomous steps taken toward the current objective run (reset when a new
    # human task/message arrives). Guards the loop against runaway continuation.
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The agent's self-reported mission progress (0-100) and a short label,
    # parsed from its ``PROGRESS:`` directives — drives the dashboard progress bar.
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # When ``status == "blocked"``, the decision/question the agent raised for a
    # human (via a ``NEED_INPUT:`` directive). Answered by sending a follow-up.
    blocked_question: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Owned by a single workflow step rather than being a reusable unit: its
    # prompt lives with the step, it never appears in the Agents list, and it is
    # deleted when the step that owns it goes away. This is the execution vessel
    # behind an ``inline`` step — the runtime is agent-keyed, so a step still
    # needs *an* agent to drive, but the user never has to manage it.
    inline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    # --- Capability toggles --------------------------------------------------
    # What this agent may draw on. Defaults keep today's behaviour (everything
    # on). Turning things off is a real lever on both cost and focus: a step that
    # only has to rewrite a paragraph doesn't need the whole MCP tool catalogue
    # in its context, and a "pure transform" step is better off *not* consulting
    # long-term memory. A workflow step can override each of these per step.
    use_mcp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    use_skills: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    use_memory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    # Per-agent approval policy override gating tool calls ("manual" /
    # "balanced" / "autonomous"). ``None`` means inherit the global default
    # (``agents_approval_policy``), so a fleet can keep one cautious default
    # while individual trusted missions run more (or less) autonomously.
    approval_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # --- Fleet governance / budgets -----------------------------------------
    # Optional cap on cumulative tokens (input+output) this agent may spend
    # before the governor parks it as ``blocked`` for a human decision. Null =
    # ungoverned. The running totals below feed both this cap and the aggregate
    # observability metrics; they accumulate across turns via ``_record_usage``.
    token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # --- Retry / auto-recovery ----------------------------------------------
    # How many times to automatically re-run this agent after a ``failed`` turn
    # before giving up. 0 = no auto-retry. ``retry_count`` tracks attempts so
    # far; ``next_retry_at`` is the scheduler's due time for the backoff re-run.
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Provenance: the blueprint this agent was stamped from, if any.
    blueprint_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_blueprints.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # When the agent reached a terminal state (completed/failed/cancelled). Used
    # for aggregate throughput/duration metrics on the dashboard.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The prompt for the turn currently in flight: set when a task/follow-up is
    # sent, cleared once the turn finishes and is posted back. Unlike the
    # in-memory ``_LiveSession.pending_prompt`` this survives a restart, so a
    # turn interrupted mid-flight can be re-sent on resume and still notify back.
    active_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True
    )
    # Short human-facing summary of the outcome (e.g. the agent's last message),
    # surfaced in the list without replaying the full event history.
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Optional container link (topic *or* chat). Detach = set back to NULL.
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Assistant Role appended to the agent's system preamble to give it a
    # persistent persona. Null resolves to the default role (no persona
    # injected). SET NULL on delete reverts to default.
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Last time the runtime reported activity for this session (event arrival).
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamp of the last time the user opened this agent session. Used to
    # compute the Agents-list unread badge: assistant replies produced after
    # this are unread. Null means "never explicitly opened" — treated as fully
    # read (mirrors Topic/Chat.last_read_at) so background history doesn't show
    # as unread retroactively.
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Non-null once the session is archived (hidden from the active list but kept
    # for history). Mirrors Topic/Chat archiving.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    topic: Mapped[Topic | None] = relationship("Topic")
    chat: Mapped[Chat | None] = relationship("Chat")

    # Recurrence config + run state when the agent re-runs on a cadence. One-to-
    # one; null for unscheduled agents. Deleting the agent cascades to it.
    # Eager-loaded (selectin) so the API can serialise it without an async lazy
    # load, mirroring how the agents router returns refreshed ORM rows directly.
    schedule: Mapped[AgentSchedule | None] = relationship(
        "AgentSchedule",
        back_populates="agent_session",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    # External event triggers (webhooks) that can start this agent.
    triggers: Mapped[list[AgentTrigger]] = relationship(
        "AgentTrigger",
        back_populates="agent",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Published blackboard outputs, newest first.
    artifacts: Mapped[list[AgentArtifact]] = relationship(
        "AgentArtifact",
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentArtifact.created_at.desc()",
        lazy="selectin",
    )
