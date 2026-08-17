"""AgentSession model — the *definition* of a long-running Copilot SDK agent.

Agents mode runs deferred, autonomous work through the GitHub Copilot SDK, which
owns the agent loop **and** the durable session state on disk (keyed by the run's
``copilot_session_id`` under the app's ``agents_home``). So Precursor keeps only
a thin definition row here: the objective, the capability defaults, the optional
container it's linked to (a topic *or* a chat, mirroring ``Message``/``Reminder``),
and a denormalised status cache so the Agents tab can list agents without booting
the runtime.

**This row is the reusable definition, not an execution.** A single execution
lives in ``AgentRun`` (see ``models/agent_run.py``), which owns the SDK handle,
the capability snapshot and the per-run counters. That split is what lets two
workflows drive the same agent at once without corrupting each other.

The execution columns below (``status``, ``active_prompt``, ``blocked_question``,
``step_count``, ``progress``, ``result_summary``, ``error``, ``finished_at``,
``last_activity_at`` and the token totals) are retained as a **denormalised
mirror of the agent's most recent run**, so the Agents list, inbox, metrics and
fleet governor keep reading one row. ``AgentRun`` is authoritative; the mirror is
a cache, written in exactly one place (``_mirror_run_to_agent``).

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
    from precursor.backend.models.agent_run import AgentRun
    from precursor.backend.models.agent_schedule import AgentSchedule
    from precursor.backend.models.agent_state import AgentState
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

    # The *public* identifier used in deep links (``/agents/{uuid}``), the
    # ``/agent`` command and transfer lookups. We mint it (a UUID) at row
    # creation so every agent has a stable, shareable id from the moment it
    # exists — even before it has ever run.
    #
    # This used to be the SDK's ``copilot_session_id``, which conflated "how a
    # human addresses this agent" with "which live SDK session it is talking
    # to". The SDK handle now lives on ``AgentRun`` (one per execution); this is
    # the durable address of the definition. The migration copies each agent's
    # old ``copilot_session_id`` here so existing links keep resolving.
    public_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Pointer to the execution this agent is "currently" on — the newest run,
    # live or not. Disambiguates agent-addressed dispatch (a follow-up message
    # names an *agent*, not a run) and backs the denormalised mirror below.
    # ``use_alter`` because agent_sessions <-> agent_runs is a cycle.
    current_run_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "agent_runs.id", ondelete="SET NULL", use_alter=True, name="fk_agent_current_run"
        ),
        nullable=True,
    )

    # Portable identity for YAML export/import. Unlike ``public_id`` (this
    # install's address for the agent), this is a plain UUID that travels *with*
    # the definition, so re-importing a file that originally came from this agent
    # recognises it as the same object instead of falling back to a fuzzy title
    # match. Nullable for legacy rows; minted lazily on first export.
    export_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
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
    # MIRROR of ``AgentRun.step_count`` for the current run.
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The agent's self-reported mission progress (0-100) and a short label,
    # parsed from its ``PROGRESS:`` directives — drives the dashboard progress bar.
    # MIRROR of ``AgentRun.progress`` / ``AgentRun.progress_label``.
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # When ``status == "blocked"``, the decision/question the agent raised for a
    # human (via a ``NEED_INPUT:`` directive). Answered by sending a follow-up.
    # MIRROR of ``AgentRun.blocked_question``.
    blocked_question: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Owned by a single workflow step rather than being a reusable unit: its
    # prompt lives with the step, it never appears in the Agents list, and it is
    # deleted when the step that owns it goes away. This is the execution vessel
    # behind an ``inline`` step. Historically the runtime was agent-keyed, so a
    # step needed *an* agent to drive; now that executions are ``AgentRun`` rows
    # this is purely a UX affordance — an unlisted, step-private definition.
    inline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    # --- Capability defaults -------------------------------------------------
    # What this agent may draw on. Defaults keep today's behaviour (everything
    # on). Turning things off is a real lever on both cost and focus: a step that
    # only has to rewrite a paragraph doesn't need the whole MCP tool catalogue
    # in its context, and a "pure transform" step is better off *not* consulting
    # long-term memory.
    #
    # These are **defaults**, not live scope: a workflow step's per-step
    # overrides are snapshotted onto the ``AgentRun`` at launch and never written
    # back here, so two workflows can scope the same agent differently at once.
    use_mcp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    use_skills: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    use_memory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Narrows ``use_mcp`` from "the whole catalogue" to named servers, comma-
    # separated. Null = every enabled server.
    mcp_servers: Mapped[str | None] = mapped_column(String(400), nullable=True)

    # Per-agent approval policy override gating tool calls ("manual" /
    # "balanced" / "autonomous"). ``None`` means inherit the global default
    # (``agents_approval_policy``), so a fleet can keep one cautious default
    # while individual trusted missions run more (or less) autonomously.
    approval_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # --- Fleet governance / budgets -----------------------------------------
    # Optional cap on cumulative tokens (input+output) this agent may spend
    # before the governor parks it as ``blocked`` for a human decision. Null =
    # ungoverned. The cap is deliberately *cumulative across runs* — it governs
    # the agent, not one execution — while the per-run spend lives on
    # ``AgentRun``. The totals below are the running sum, kept in step with the
    # runs via ``_record_usage``.
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
    # MIRROR of ``AgentRun.finished_at``.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The prompt for the turn currently in flight: set when a task/follow-up is
    # sent, cleared once the turn finishes and is posted back. Unlike the
    # in-memory ``_LiveSession.pending_prompt`` this survives a restart, so a
    # turn interrupted mid-flight can be re-sent on resume and still notify back.
    # MIRROR of ``AgentRun.active_prompt``.
    active_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # MIRROR of ``AgentRun.status`` for ``current_run``. Indexed because the
    # Agents list, the inbox, the fleet governor and the retry sweeper all filter
    # on it without wanting a join.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True
    )
    # Short human-facing summary of the outcome (e.g. the agent's last message),
    # surfaced in the list without replaying the full event history.
    # MIRROR of ``AgentRun.result_summary`` / ``AgentRun.error``.
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
    # MIRROR of ``AgentRun.last_activity_at``.
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

    # Executions of this agent, newest first. Deliberately *not* eager-loaded:
    # an agent accumulates runs indefinitely and nothing in the list
    # serialisation needs them — the mirror columns above cover that. Fetched
    # through the runs API, or resolved one at a time via ``current_run_id``.
    runs: Mapped[list[AgentRun]] = relationship(
        "AgentRun",
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentRun.id.desc()",
        foreign_keys="AgentRun.agent_id",
    )

    # Published blackboard outputs, newest first. Scoped to a run in practice
    # (``AgentArtifact.agent_run_id``); this relationship is the agent-wide view
    # used for cascade deletes and the "everything this agent ever produced"
    # listing.
    artifacts: Mapped[list[AgentArtifact]] = relationship(
        "AgentArtifact",
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentArtifact.created_at.desc()",
        lazy="selectin",
    )

    # Private cross-run scratchpad. Unlike ``artifacts`` this is deliberately
    # *not* eager-loaded: state bodies can be sizeable and nothing in the agent
    # serialisation needs them, so they're fetched only through the state API.
    # The cascade still resolves on delete because ``AsyncSession.delete`` is
    # awaited (it loads unloaded cascades), which also keeps cleanup correct on
    # SQLite, where ``ON DELETE CASCADE`` is inert with foreign keys off.
    states: Mapped[list[AgentState]] = relationship(
        "AgentState",
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentState.key",
    )
