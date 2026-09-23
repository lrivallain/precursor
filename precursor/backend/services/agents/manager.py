"""AgentManager — owns the Copilot SDK runtime and live agent sessions.

This is the bridge between Precursor's ``AgentSession`` *definitions*, their
``AgentRun`` *executions*, and the Copilot SDK's out-of-process agent runtime.
One ``CopilotClient`` (one CLI server) is started for the app's lifetime; each
**run** maps to one persistent SDK session (keyed by the run's
``copilot_session_id``, state stored under ``agents_home``), so sessions survive
restarts and can be resumed.

Two agents' worth of state used to live on one row, which meant two workflows
pointing a step at the same agent silently shared a live session, a status and a
token ledger. Live sessions are therefore keyed by ``agent_run.id``: concurrent
executions of one definition each get their own SDK session, capability snapshot
and counters. The durable *timeline* caches (``_events``/``_loaded``) stay keyed
by agent, because the visible transcript is per agent and outlives any one run.

Responsibilities:

* Lifespan ``start``/``stop`` (gated on the enabled preference + capability
  probe — a no-op when Agents mode is off or the SDK is absent).
* Create/resume SDK sessions and attach the ``precursor`` MCP server so the
  agent can read topic context and post results back (``post_message``), plus
  every other catalog MCP server (built-in or user-defined) the user has
  enabled in Settings.
* Bridge SDK events → DB status cache + ``agent.changed`` bus signals, and post a
  system message to the linked container when a task finishes.
* Apply the permission policy: auto-approve read-only + precursor MCP; park
  writes/shell as ``needs_approval`` until the user resolves them.

All SDK objects are treated as ``Any`` (loaded lazily via
``services.agents.runtime``) so this module imports cleanly without the optional
dependency installed.

The pure pieces live in leaf modules this one only imports from: the agent text
protocol in ``directives``, MCP server scoping in ``mcp_scope``, SDK shutdown
log filtering in ``sdk_logging`` and the live-session record in
``live_session``. Cohesive method groups are delegated to collaborators the
manager owns, each holding a back-reference and reading shared state through it
at call time: MCP config assembly (``mcp_config``), permission handling
(``permissions``), slash commands (``commands``), the event timeline
(``timeline``), model selection (``models``), token metering (``usage``), the
artifact blackboard (``artifacts``) and the system preamble (``prompting``).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import delete, select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentEventRecord,
    AgentRun,
    AgentSession,
    Chat,
    Message,
    MessageRole,
    Topic,
)
from precursor.backend.schemas.agent import AgentEvent, AgentEventPage
from precursor.backend.services.agents import (
    commands,
    fleet,
    mcp_config,
    permissions,
    prompting,
    runtime,
    timeline,
    usage,
)
from precursor.backend.services.agents.artifacts import ArtifactStore, clear_artifacts
from precursor.backend.services.agents.directives import (
    _CONTINUE_NUDGE,
    RESULT_SUMMARY_CAP,
    _clean_narration,
    parse_agent_command,
    parse_agent_directives,
    strip_control_directives,
)
from precursor.backend.services.agents.event_normalizer import normalize_event
from precursor.backend.services.agents.live_session import _Caps, _LiveSession
from precursor.backend.services.agents.mcp_config import (
    _OAUTH_FALLBACK_TTL,
    _OAUTH_REFRESH_MARGIN,
    MCPConfigBuilder,
)
from precursor.backend.services.agents.mcp_scope import (
    MCP_SCOPE_MAX_LEN,
    normalize_mcp_scope,
    parse_mcp_scope,
    scope_includes_precursor,
)
from precursor.backend.services.agents.models import ModelSelector
from precursor.backend.services.agents.permissions import PermissionBroker
from precursor.backend.services.agents.sdk_logging import _quiet_sdk_teardown_pipe_noise
from precursor.backend.services.agents.timeline import Timeline
from precursor.backend.services.agents.usage import UsageMeter
from precursor.backend.services.app_settings import (
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_enabled,
    resolve_agents_reasoning_effort,
    resolve_agents_watchdog_timeout,
)
from precursor.backend.services.events import (
    publish_agent_changed,
    publish_message_changed,
    publish_message_changed_chat,
    set_current_client_id,
)
from precursor.backend.services.suggestions import split_suggestions

# The pure helpers and the live-session record moved to their own modules;
# listing them here keeps existing ``from …agents.manager import …`` callers
# working (and explicit for mypy).
__all__ = [
    "MCP_SCOPE_MAX_LEN",
    "RESULT_SUMMARY_CAP",
    "_OAUTH_FALLBACK_TTL",
    "_OAUTH_REFRESH_MARGIN",
    "AgentManager",
    "_LiveSession",
    "get_agent_manager",
    "normalize_mcp_scope",
    "parse_agent_command",
    "parse_agent_directives",
    "parse_mcp_scope",
    "scope_includes_precursor",
    "strip_control_directives",
]

logger = logging.getLogger(__name__)

# An agent reaching one of these has ended its turn, which is what a workflow
# waits on to advance. ``needs_approval`` is included because the coordinator
# uses that pass to surface the approve/deny card on the workflow board, not
# because it moves the run on.
_RESTING_STATUSES = (
    "idle",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "needs_approval",
)

# Execution state written by :meth:`AgentManager._patch_run`. ``_MIRRORED`` lands
# on the ``AgentRun`` *and* is copied onto its ``AgentSession`` (the denormalised
# view the Agents list, inbox, metrics, fleet governor and scheduler read).
# ``_RUN_ONLY`` has no column on the definition and stays on the run.
_MIRRORED_RUN_FIELDS = frozenset(
    {
        "status",
        "active_prompt",
        "blocked_question",
        "result_summary",
        "error",
        "step_count",
        "progress",
        "progress_label",
        "total_input_tokens",
        "total_output_tokens",
        "finished_at",
        "last_activity_at",
    }
)
_RUN_ONLY_FIELDS = frozenset(
    {
        "copilot_session_id",
        "started_at",
        "stall_count",
        "last_progress",
        "workflow_run_id",
        "workflow_run_step_id",
    }
)

# Cap how long we wait for the out-of-process runtime to come up so a stuck or
# unauthenticated CLI can't block app startup or a settings save indefinitely.
_START_TIMEOUT_SECONDS = 30.0

# How often the watchdog sweeps for stalled running sessions.
_WATCHDOG_INTERVAL_SECONDS = 60.0


def _runtime_env() -> dict[str, str]:
    """Environment for the spawned CLI, pinned to the binary the probe resolved.

    Left to itself the SDK resolves ``COPILOT_CLI_PATH`` and then *downloads* the
    CLI when its cache is empty — so a machine whose only Copilot CLI is on
    ``PATH`` would pass the capability probe and then pull ~90 MB at startup, and
    could even end up driving a different binary than the one Settings reported.
    Handing the probe's answer back over the SDK's own env contract keeps the two
    in agreement and keeps startup off the network.
    """
    env = dict(os.environ)
    resolved = runtime.runtime_binary_path()
    if resolved:
        env["COPILOT_CLI_PATH"] = resolved
    return env


# --- Autonomy goal loop --------------------------------------------------------
# After this many consecutive no-progress continuation steps, the loop stops and
# parks the agent as ``blocked`` so a human can course-correct instead of letting
# it spin. Kept small — autonomy is about steady progress, not infinite retries.
_STALL_LIMIT = 3


class AgentManager:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._ready = False
        # Live SDK sessions, keyed by ``agent_run.id`` — one per *execution*, not
        # per definition. Two workflows driving the same agent concurrently each
        # get their own session, grants, approval policy and pending prompt.
        self._live: dict[int, _LiveSession] = {}
        # Durable per-agent timeline. The SDK's ``get_events`` is per-connection
        # (a resumed session only replays ``SessionStartData``), so we archive
        # every streamed event. This in-memory copy is a write-through cache over
        # the ``agent_events`` table: it survives ``teardown_session`` (e.g. on
        # topic link) and, because every event is also persisted, the timeline is
        # reloaded from the DB after a process restart (see ``Timeline.ensure_loaded``).
        # Deliberately keyed by *agent*, not run: the visible transcript spans an
        # agent's whole history (each record carries ``agent_run_id`` so a single
        # run's slice can still be filtered out).
        # Cleared only when the agent is deleted.
        self._events: dict[int, list[AgentEvent]] = {}
        # Agents whose DB archive has been hydrated into ``_events`` this process.
        self._loaded: set[int] = set()
        self._events_lock = asyncio.Lock()
        # Per-agent locks serialising event handling so SDK events are processed
        # in arrival order — otherwise an idle handler can race ahead of the
        # assistant-message handler and post a stale answer back to the topic.
        # Per *agent* rather than per run on purpose: ``_notify_back`` posts into
        # the agent's shared topic/chat, so concurrent runs must still take turns.
        self._event_locks: dict[int, asyncio.Lock] = {}
        # Per-run locks serialising session build/resume. ``_ensure_live`` does a
        # check-then-create (read ``_live``, ``create_session``, write ``_live``);
        # without this lock two concurrent callers — e.g. ``start_task`` racing the
        # timeline's ``get_events`` when the detail page is open at startup — both
        # see no cached session and each issue a ``session.create`` for the same
        # ``copilot_session_id``. The duplicate create leaves the CLI's permission
        # responder mis-wired, so every tool call is denied non-interactively
        # ("Permission denied and could not request permission from user").
        self._live_locks: dict[int, asyncio.Lock] = {}
        # ``agent.id -> agent_run.id`` for the agent's *current* run. A synchronous
        # index over ``current_run_id`` so hot, no-``await`` readers (the dashboard
        # cockpit's ``live_activity``) can reach an agent's live session without a
        # DB round-trip. Populated as runs open and pruned on teardown; readers
        # must tolerate a miss and fall back to the DB.
        self._agent_runs: dict[int, int] = {}
        # Per-agent set of OAuth servers we've already surfaced a sign-in prompt
        # for, so a held session doesn't re-announce ``mcp_auth_required`` on
        # every rebuild/tool error. Cleared once the server attaches with valid
        # creds (so a later token expiry re-announces) or the agent is forgotten.
        self._auth_announced: dict[int, set[str]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task[Any] | None = None
        # Collaborators owning cohesive method groups. Each reads the shared
        # state above through its back-reference, so tests that swap ``_live``
        # or patch a manager method still reach them.
        self._mcp = MCPConfigBuilder(self)
        self._permissions = PermissionBroker(self)
        self._transcript = Timeline(self)
        self._model_selection = ModelSelector(self)
        self._usage = UsageMeter(self)
        self._artifacts = ArtifactStore(self)

    @property
    def ready(self) -> bool:
        return self._ready

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Start the runtime if Agents mode is enabled and usable."""
        async with self._lock:
            if self._ready:
                return
            # Un-stick agents that were mid-turn when the process last died. This
            # runs on every boot BEFORE we try to bring the SDK client up (and
            # regardless of whether it succeeds), because a reload can orphan a
            # ``running`` row with no live task behind it: if the client then
            # fails to start, ``_ready`` stays False, the watchdog never runs,
            # and the row would otherwise stay pinned in ``running`` forever.
            await self._mark_interrupted_on_boot()
            async with SessionLocal() as session:
                enabled = await resolve_agents_enabled(session)
            ok, detail = runtime.agents_available()
            if not enabled:
                logger.info("Agents mode disabled — runtime not started.")
                return
            if not ok:
                logger.warning("Agents mode enabled but unavailable: %s", detail)
                return
            try:
                sdk = runtime.load_sdk()
                self._client = sdk.CopilotClient(
                    base_directory=runtime.agents_home_dir(),
                    env=_runtime_env(),
                    log_level=get_settings().log_level,
                )
                await asyncio.wait_for(self._client.start(), timeout=_START_TIMEOUT_SECONDS)
            except Exception:
                logger.exception("Failed to start Copilot SDK client")
                with contextlib.suppress(Exception):
                    if self._client is not None:
                        await self._client.stop()
                self._client = None
                return
            self._ready = True
            logger.info("Agents runtime started (%s).", detail)
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        async with self._lock:
            if not self._ready:
                return
            self._ready = False
            # Quiet the SDK's expected broken-pipe tracebacks: on Ctrl+C the CLI
            # child shares our process group and may die before these graceful
            # calls finish writing to it (see ``_quiet_sdk_teardown_pipe_noise``).
            with _quiet_sdk_teardown_pipe_noise():
                # Unblock any parked permission requests so awaiting tasks unwind.
                for live in self._live.values():
                    for fut in live.pending.values():
                        if not fut.done():
                            fut.set_result(self._reject("runtime shutting down"))
                for live in list(self._live.values()):
                    with contextlib.suppress(Exception):
                        await live.sdk_session.disconnect()
                self._live.clear()
                if self._client is not None:
                    with contextlib.suppress(Exception):
                        await self._client.stop()
                self._client = None
        for task in list(self._tasks):
            task.cancel()
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None

    async def _mark_interrupted_on_boot(self) -> None:
        """Flag executions that were mid-turn when the process last died.

        The runs are the authoritative rows and must be flipped too: they are
        what the fleet governor counts, and nothing else ever revisits them, so a
        run left ``running`` by a crash would hold one of the
        ``agents_max_concurrent`` slots for the lifetime of the install.
        """
        async with SessionLocal() as session:
            from sqlalchemy import update

            await session.execute(
                update(AgentRun).where(AgentRun.status == "running").values(status="interrupted")
            )
            await session.execute(
                update(AgentSession)
                .where(AgentSession.status == "running")
                .values(status="interrupted")
            )
            await session.commit()

    # ------------------------------------------------------------------ watchdog

    async def _watchdog_loop(self) -> None:
        """Periodically interrupt running sessions that have gone silent.

        A turn can wedge (a hung tool, a dropped runtime connection) and leave a
        session pinned in ``running`` forever, never notifying back. This sweep
        flips such sessions to ``interrupted`` (resumable) with a reason, so they
        surface in the UI and the user can Resume to retry the in-flight prompt.
        """
        while self._ready:
            try:
                await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
                await self._watchdog_sweep()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("agent watchdog sweep failed", exc_info=True)

    async def _watchdog_sweep(self) -> None:
        """Interrupt every *run* that has gone silent, not every agent.

        The sweep is run-scoped for two reasons. ``AgentSession.status`` and
        ``last_activity_at`` only mirror the agent's *current* run, so a wedged
        run that some other execution has since superseded would be invisible
        here and stay pinned in ``running`` forever — leaking a concurrency slot
        from the fleet governor, which counts runs. And the teardown has to hit
        only the wedged run: an agent shared by two workflows may have a second,
        perfectly healthy session mid-turn that must survive the sweep.
        """
        async with SessionLocal() as session:
            timeout = await resolve_agents_watchdog_timeout(session)
            cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
            rows = (
                await session.execute(
                    select(AgentRun, AgentSession.topic_id, AgentSession.chat_id)
                    .join(AgentSession, AgentSession.id == AgentRun.agent_id)
                    .where(AgentRun.status == "running")
                )
            ).all()
            stale: list[tuple[int, int, int | None, int | None]] = []
            for run, topic_id, chat_id in rows:
                ref = run.last_activity_at or run.started_at or run.updated_at or run.created_at
                if ref is None:
                    continue
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=UTC)
                if ref < cutoff:
                    stale.append((run.id, run.agent_id, topic_id, chat_id))
        reason = (
            f"No runtime activity for over {max(1, timeout // 60)} min — "
            "interrupted by the watchdog. Resume to retry."
        )
        # Drop the wedged live session so a Resume rebuilds it clean, then signal
        # the UI. Done outside the read transaction to keep it tight, and through
        # ``_patch_run`` so the agent-level mirror is maintained in one place.
        for run_id, agent_id, topic_id, chat_id in stale:
            logger.warning(
                "agent %s run %s: interrupted by watchdog (idle > %ss)", agent_id, run_id, timeout
            )
            await self._patch_run(run_id, status="interrupted", error=reason)
            with contextlib.suppress(Exception):
                await self._teardown_run(run_id)
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=topic_id,
                chat_id=chat_id,
                agent_run_id=run_id,
            )

    # ------------------------------------------------------------------ helpers

    def _spawn(self, coro: Any) -> None:
        # Agent work runs asynchronously in the background, but ``create_task``
        # copies the caller's context — which carries the originating request's
        # ``X-Client-Id`` (set by middleware). Every event this task publishes
        # (live progress *and* the notify-back that marks a linked topic/chat
        # unread) would then be stamped with that id and echo-suppressed in the
        # very tab that started the agent, while other tabs see it. The agent's
        # results aren't "live-streamed" back to the originating tab the way a
        # chat turn is, so clear the client id for the task: its events broadcast
        # to *every* subscriber, including the originator.
        ctx = contextvars.copy_context()
        ctx.run(set_current_client_id, None)
        task = asyncio.create_task(coro, context=ctx)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def enqueue(self, coro: Any) -> None:
        """Schedule a manager coroutine as a tracked background task."""
        self._spawn(coro)

    def _require_ready(self) -> None:
        if not self._ready or self._client is None:
            _ok, detail = runtime.agents_available()
            raise RuntimeError(f"Agents runtime not available: {detail}")

    async def _enabled_catalog_fingerprint(
        self, scope: frozenset[str] | None = None
    ) -> frozenset[str]:
        return await self._mcp.enabled_catalog_fingerprint(scope)

    async def _expected_mcp_fingerprint(self, caps: _Caps) -> frozenset[str]:
        return await self._mcp.expected_mcp_fingerprint(caps)

    async def _catalog_mcp_configs(
        self, scope: frozenset[str] | None = None
    ) -> tuple[dict[str, Any], datetime | None, list[str]]:
        return await self._mcp.catalog_mcp_configs(scope)

    # Aliased rather than delegated: collaborators call these through the manager
    # at call time, so patching them on the class (as tests do) still reaches them.
    _entry_to_sdk_config = staticmethod(mcp_config.entry_to_sdk_config)
    _oauth_bearer_header = staticmethod(mcp_config.oauth_bearer_header)
    _auth_skipped_stamps = staticmethod(mcp_config.auth_skipped_stamps)
    _oauth_stale = staticmethod(mcp_config.oauth_stale)
    _auth_server_from_failed_tool = staticmethod(mcp_config.auth_server_from_failed_tool)
    _blocked_on_missing_auth = staticmethod(mcp_config.blocked_on_missing_auth)

    # ------------------------------------------------------------------ runs

    async def _open_run(
        self,
        agent_id: int,
        *,
        trigger: str = "manual",
        workflow_run_id: int | None = None,
        workflow_run_step_id: int | None = None,
        inherit_session: bool = False,
    ) -> AgentRun | None:
        """Start a new execution of ``agent_id`` and make it the agent's current run.

        A run snapshots the capabilities it will execute under (model, MCP/skills/
        memory toggles, server scope, role, approval policy) so a later edit to the
        definition — or a concurrent workflow step applying its own overrides —
        can't change what an in-flight execution is running with.

        ``inherit_session`` carries the previous run's ``copilot_session_id`` over
        so the SDK resumes the same conversation (``clear_session(keep_id=True)``,
        scheduled ``/agent <uuid>`` nudges). Otherwise the run starts with no
        handle and mints one on its first connect.
        """
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_id)
            if agent is None:
                return None
            prior: AgentRun | None = None
            if agent.current_run_id is not None:
                prior = await session.get(AgentRun, agent.current_run_id)
            run = AgentRun(
                agent_id=agent.id,
                trigger=trigger,
                workflow_run_id=workflow_run_id,
                workflow_run_step_id=workflow_run_step_id,
                copilot_session_id=(
                    prior.copilot_session_id if inherit_session and prior is not None else None
                ),
                status="pending",
                model=agent.model,
                use_mcp=agent.use_mcp,
                use_skills=agent.use_skills,
                use_memory=agent.use_memory,
                mcp_servers=agent.mcp_servers,
                approval_policy=agent.approval_policy,
                role_id=agent.role_id,
                started_at=datetime.now(UTC),
            )
            session.add(run)
            await session.flush()
            agent.current_run_id = run.id
            await session.commit()
            await session.refresh(run)
            self._agent_runs[agent.id] = run.id
            return run

    async def _resolve_run(self, agent_id: int) -> AgentRun | None:
        """Return the agent's current run, opening one if it has never executed.

        Dispatch is agent-addressed everywhere the user can reach it (routers,
        slash commands, the scheduler), so "which execution?" has to be answered
        here. Agents migrated from before runs existed, and agents whose run was
        deleted, get a fresh one rather than failing the call.
        """
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_id)
            if agent is None:
                return None
            if agent.current_run_id is not None:
                run = await session.get(AgentRun, agent.current_run_id)
                if run is not None:
                    self._agent_runs[agent_id] = run.id
                    return run
        return await self._open_run(agent_id, trigger="manual")

    async def _run(self, run_id: int) -> AgentRun | None:
        async with SessionLocal() as session:
            return await session.get(AgentRun, run_id)

    async def _load_run(self, run_id: int) -> tuple[AgentRun, AgentSession] | None:
        """Load a run together with the definition it executes, or ``None``."""
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return None
            agent = await session.get(AgentSession, run.agent_id)
            if agent is None:
                return None
            return run, agent

    async def _patch_run(self, run_id: int, **values: Any) -> None:
        """Write execution state to a run and mirror it onto its agent.

        The run is authoritative; ``AgentSession`` keeps a denormalised copy of
        the *current* run's state so the Agents list, inbox, metrics, fleet
        governor and scheduler keep reading a single row. Keys outside
        :data:`_MIRRORED_RUN_FIELDS` (``retry_count``, ``next_retry_at``, …) are
        definition-level and land on the agent only — this is the one place that
        split is made, so the mirror can't drift.

        The mirror is skipped when the run is no longer the agent's current one:
        a finished workflow step must not overwrite whatever is running now.
        """
        if not values:
            return
        run_only = {k: v for k, v in values.items() if k in _RUN_ONLY_FIELDS}
        mirrored = {k: v for k, v in values.items() if k in _MIRRORED_RUN_FIELDS}
        agent_only = {
            k: v
            for k, v in values.items()
            if k not in _RUN_ONLY_FIELDS and k not in _MIRRORED_RUN_FIELDS
        }
        async with SessionLocal() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            for key, value in {**run_only, **mirrored}.items():
                setattr(run, key, value)
            agent = await session.get(AgentSession, run.agent_id)
            if agent is not None:
                for key, value in agent_only.items():
                    setattr(agent, key, value)
                if agent.current_run_id == run.id:
                    for key, value in mirrored.items():
                        setattr(agent, key, value)
            await session.commit()

    # ------------------------------------------------------------------ sessions

    async def _ensure_live(self, agent: AgentSession, run: AgentRun) -> _LiveSession:
        """Return the live SDK session for ``run``, creating/resuming it.

        Sessions are keyed by *run*, not agent: two workflows driving the same
        agent concurrently each get their own SDK conversation, tool grants,
        approval policy and pending prompt. Capabilities come from the run's
        immutable snapshot, so an edit to the definition — or another step
        applying its own overrides — can't reshape a session already in flight.

        A cached session is reused unless its baked-in OAuth bearer header is
        about to expire (see :meth:`_oauth_stale`): the SDK can't refresh a static
        header, so we transparently tear the session down and recreate it, which
        re-mints the token while resuming the same conversation via
        ``copilot_session_id``. We never refresh mid-turn — only when the run is
        idle, so an in-flight turn is left untouched until its next dispatch.

        The whole check-then-create runs under a per-run lock so concurrent
        callers (e.g. ``start_task`` racing the timeline's ``get_events`` when the
        agent detail page is open at startup) can't each fire a ``session.create``
        for the same ``copilot_session_id`` — a duplicate create leaves the CLI's
        permission responder mis-wired and every tool call is then denied.
        """
        self._require_ready()
        lock = self._live_locks.setdefault(run.id, asyncio.Lock())
        async with lock:
            return await self._ensure_live_locked(agent, run)

    async def _ensure_live_locked(self, agent: AgentSession, run: AgentRun) -> _LiveSession:
        live = self._live.get(run.id)
        if live is not None:
            oauth_stale = self._oauth_stale(live)
            catalog_changed = (
                live.mcp_fingerprint is not None
                and live.mcp_fingerprint != await self._expected_mcp_fingerprint(run)
            )
            # A server we had to skip for missing credentials doesn't move the
            # fingerprint (which tracks *enabled* servers), so without this the
            # tool-less session would be reused forever. Comparing credential
            # stamps rather than names means a rebuild that still can't attach
            # re-records the same stamps and doesn't fire again.
            auth_recovered = live.mcp_auth_skipped != await self._auth_skipped_stamps(
                [name for name, _ in live.mcp_auth_skipped]
            )
            if not oauth_stale and not catalog_changed and not auth_recovered:
                return live
            if run.status in {"running", "needs_approval", "pending"}:
                # A turn is in flight — don't disrupt it; refresh on the next
                # idle dispatch instead.
                return live
            if oauth_stale:
                reason = "refresh an expiring OAuth token"
            elif catalog_changed:
                reason = "pick up a changed MCP server set"
            else:
                reason = "pick up a recovered MCP sign-in"
            logger.info("Rebuilding agent %s run %s session to %s", agent.id, run.id, reason)
            await self._teardown_run(run.id, forget=False)

        assert self._client is not None
        kwargs: dict[str, Any] = {
            "on_permission_request": self._permissions.make_handler(run.id),
        }
        # Reasoning effort + context tier are global agent prefs (Settings →
        # Agents / composer toolbar). Applied at session creation, mirroring how
        # the model is chosen — a change takes effect on the next new/rebuilt
        # session. The frontend only offers efforts the chosen model supports.
        # The model comes from the DB-resolved selection (what the composer
        # writes), not the env/config constant, so a new agent honours the
        # currently selected model instead of the factory default. An explicit
        # per-agent pin (``run.model``, snapshotted from the agent or a step
        # override) still wins.
        async with SessionLocal() as s:
            default_model = await resolve_agents_default_model(s)
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
        model = run.model or default_model
        model = await self._sanitize_model(agent.id, model)
        if model:
            kwargs["model"] = model
        if effort:
            kwargs["reasoning_effort"] = effort
        if tier and tier != "default":
            kwargs["context_tier"] = tier
        if run.copilot_session_id:
            kwargs["session_id"] = run.copilot_session_id
        # An agent with MCP switched off gets no tool servers at all — not even
        # the first-party one. Whole catalogues of tool schemas are a large,
        # fixed context cost, so a step that only has to transform text pays
        # nothing for tools it will never call. An explicitly *empty* server
        # scope says the same thing in the other vocabulary, and means it.
        scope = parse_mcp_scope(run.mcp_servers)
        scoped_to_nothing = scope is not None and not scope
        tools_on = run.use_mcp and not scoped_to_nothing
        oauth_expires_at: datetime | None = None
        auth_required: list[str] = []
        if tools_on:
            mcp: dict[str, Any] = {}
            # ``precursor`` ignores the mcp_enabled toggle (it's first-party and
            # always available) but is not exempt from the scope — see
            # scope_includes_precursor. Attached here rather than via
            # _catalog_mcp_configs so it keeps its full-access env.
            if scope_includes_precursor(scope):
                mcp.update(self._mcp.precursor_mcp_config(agent) or {})
            # Every enabled catalog server the scope allows (built-in +
            # user-defined). _catalog_mcp_configs already skips 'precursor', so
            # the first-party full-access entry above can't be shadowed.
            catalog, oauth_expires_at, auth_required = await self._catalog_mcp_configs(scope)
            mcp.update(catalog)
            if mcp:
                kwargs["mcp_servers"] = mcp
        # Snapshot what this session carries so a later toggle — or a rebuilt run
        # with a different scope — rebuilds it. Computed by the same method the
        # reuse check compares against, so the two can't drift.
        mcp_fingerprint = await self._expected_mcp_fingerprint(run)
        preamble = await prompting.system_preamble(agent, run)
        if preamble:
            # Append (don't replace) so the agent keeps its SDK base instructions
            # but also gets the operator's custom guidance and any topic binding.
            kwargs["system_message"] = {"mode": "append", "content": preamble}

        sdk_session = await self._client.create_session(**kwargs)
        live = _LiveSession(
            sdk_session=sdk_session,
            oauth_expires_at=oauth_expires_at,
            mcp_fingerprint=mcp_fingerprint,
            mcp_auth_skipped=await self._auth_skipped_stamps(auth_required),
            model_signature=(model, effort or None, tier or "default") if model else None,
        )
        self._live[run.id] = live
        # Synchronous index so ``live_activity`` (no ``await`` available) can find
        # the run driving an agent without a DB round-trip.
        self._agent_runs[agent.id] = run.id

        # Wire the event stream. The SDK invokes this synchronously; defer the
        # async work (DB + bus) onto the loop.
        sdk_session.on(lambda event: self._spawn(self._handle_event(run.id, event)))

        # The resume handle is *not* readable off ``CopilotSession`` — it exposes
        # no ``id``/``session_id`` attribute — so it is captured from the
        # ``SessionStartData`` event instead (see ``_handle_event_locked``), which
        # carries it and fires moments after this create. Probing the object here
        # silently yielded ``None`` forever, leaving every run unresumable: a
        # rebuild below then started an *empty* conversation and the agent lost
        # its task prompt and history mid-thread.

        # Any OAuth server we couldn't attach for lack of credentials is surfaced
        # as an in-app sign-in prompt (drives the global McpAuthBanner) instead of
        # leaving the agent to hit "tool not available" and improvise an answer.
        await self._announce_auth_required(agent.id, auth_required)
        return live

    async def _emit_synthetic(self, agent_id: int, event: AgentEvent) -> None:
        """Append a manager-originated event to the timeline (archive + publish).

        Used for events the SDK never sends — currently ``mcp_auth_required`` —
        so they persist in the durable timeline and reach the frontend over the
        same ``agent.changed`` bus as real SDK events.
        """
        await self._transcript.ensure_loaded(agent_id)
        event.at = datetime.now(UTC)
        self._events.setdefault(agent_id, []).append(event)
        await timeline.archive_event(agent_id, event)
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )

    async def _announce_auth_required(self, agent_id: int, servers: list[str]) -> None:
        await self._mcp.announce_auth_required(agent_id, servers)

    async def refresh_oauth_sessions(self) -> None:
        """Drop idle live sessions after an interactive MCP sign-in.

        The SDK bakes a static OAuth bearer into the session at creation and
        can't refresh it in place, so a session built before sign-in still lacks
        the server's tools. Tearing the idle ones down forces the next dispatch
        to rebuild with the fresh credentials; in-flight turns are left untouched
        (they refresh on their next idle dispatch via :meth:`_oauth_stale`).
        Safe to call when agents are disabled — it's a no-op until the runtime is
        ready.
        """
        if not self.ready:
            return
        # ``_live`` is keyed by *run*, so the busy check has to read the run's own
        # status and the teardown has to be run-scoped: tearing down by agent id
        # would take out every other live execution of that agent as collateral.
        for run_id in list(self._live):
            run = await self._run(run_id)
            if run is not None and run.status in {"running", "needs_approval", "pending"}:
                continue
            await self._teardown_run(run_id, forget=False)
            if run is not None:
                self._auth_announced.pop(run.agent_id, None)

    async def start_task(
        self,
        agent_id: int,
        extra_context: str | None = None,
        *,
        run_id: int | None = None,
        trigger: str = "manual",
    ) -> None:
        """Begin a fresh execution of the agent's objective.

        Fresh human intent is a *new execution*: unless the caller supplies a run
        (workflow steps open theirs in ``_launch_step`` so the step and the run
        are linked), a new :class:`AgentRun` is opened here. The step/stall/prompt
        resets below then fall out of starting on a new row rather than
        destroying whatever another workflow is running on the same agent.
        """
        run: AgentRun | None = None
        try:
            run = (
                await self._run(run_id)
                if run_id is not None
                else await self._open_run(agent_id, trigger=trigger)
            )
            if run is None:
                return
            agent = await self._load(agent_id)
            if agent is None:
                return
            # A fresh objective run starts from a clean blackboard: drop any
            # artifacts *this run* published so the new turn's deliverables
            # replace them rather than piling up beside stale ones. Scoped to the
            # run, so a concurrent workflow's blackboard is untouched.
            await self._clear_artifacts(run.id)
            live = await self._ensure_live(agent, run)
            # A step that named a server it couldn't sign in to must not run: the
            # agent would answer from its own knowledge and the workflow would
            # record that as a success. Park it so the run pauses on the sign-in.
            blocked_on = self._blocked_on_missing_auth(agent, live)
            if blocked_on:
                await self._block_turn(run.id, blocked_on)
                return
            await self._permissions.release_parked_turn(agent_id, live)
            await self._model_selection.sync_selected_model(agent, run)
            live.approval_policy = await self._approval_policy(agent, run)
            prompt = (agent.task_prompt or "").strip() or None
            live.pending_prompt = prompt
            live.pending_answer = None
            # Fresh human intent → reset the autonomy budget and stall tracking so
            # the objective run starts from a clean step count.
            live.stall_count = 0
            live.last_progress = None
            await self._patch_run(
                run.id,
                status="running",
                error=None,
                active_prompt=prompt,
                step_count=0,
                blocked_question=None,
            )
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=agent.topic_id,
                chat_id=agent.chat_id,
                agent_run_id=run.id,
            )
            # Fleet dependents receive their upstreams' published artifacts as a
            # kickoff preamble ahead of the durable objective, so results flow
            # down the DAG without a human relaying them.
            sent = agent.task_prompt
            if extra_context:
                sent = f"{extra_context}\n\n---\n\n{agent.task_prompt}"
            await live.sdk_session.send(sent)
        except Exception as exc:
            if run is not None:
                await self._fail_turn(run.id, exc)

    async def restart_with_task(self, agent_id: int, *, trigger: str = "manual") -> None:
        """Re-establish the SDK session after the task prompt was edited.

        The task is delivered only by :meth:`start_task`; a live or resumed
        session keeps the *previous* instructions in its history, so an edited
        ``task_prompt`` stays inert until it is replayed. Drop the in-memory
        session (``forget=False`` keeps the visible timeline) so the next connect
        refreshes the system preamble, then replay the new task on a new run that
        inherits the SDK handle.

        The ``copilot_session_id`` is deliberately carried over: scheduled
        ``/agent <uuid>`` nudges target the agent's ``public_id`` and the SDK
        conversation should continue. Callers that want a clean-slate context use
        the ``clear`` command instead.
        """
        await self.teardown_session(agent_id)
        run = await self._open_run(agent_id, trigger=trigger, inherit_session=True)
        await self.start_task(agent_id, run_id=run.id if run else None)

    async def send_message(self, agent_id: int, text: str) -> None:
        run: AgentRun | None = None
        try:
            agent = await self._load(agent_id)
            if agent is None:
                return
            run = await self._resolve_run(agent_id)
            if run is None:
                return
            live = await self._ensure_live(agent, run)
            await self._model_selection.sync_selected_model(agent, run)
            live.approval_policy = await self._approval_policy(agent, run)
            prompt = text.strip() or None
            live.pending_prompt = prompt
            live.pending_answer = None
            # A human message is fresh intent: reset the autonomy budget and, if
            # the agent had parked itself with a question, clear that block — this
            # message is the answer that unsticks it.
            live.stall_count = 0
            live.last_progress = None
            await self._patch_run(
                run.id,
                status="running",
                active_prompt=prompt,
                step_count=0,
                blocked_question=None,
            )
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=agent.topic_id,
                chat_id=agent.chat_id,
                agent_run_id=run.id,
            )
            await live.sdk_session.send(text)
        except Exception as exc:
            if run is not None:
                await self._fail_turn(run.id, exc)

    async def resume(self, agent_id: int) -> None:
        """Re-run the in-flight turn of an interrupted session.

        Re-sends the persisted ``active_prompt`` (the turn cut off by a restart
        or the watchdog) so it finishes and notifies back. A no-op when there's
        nothing tracked to resume.
        """
        agent = await self._load(agent_id)
        if agent is None:
            return
        run = await self._resolve_run(agent_id)
        if run is None:
            return
        prompt = (run.active_prompt or "").strip()
        if not prompt:
            return
        try:
            live = await self._ensure_live(agent, run)
            await self._model_selection.sync_selected_model(agent, run)
            live.approval_policy = await self._approval_policy(agent, run)
            live.pending_prompt = prompt
            live.pending_answer = None
            await self._patch_run(run.id, status="running", error=None)
            await publish_agent_changed(
                agent_session_id=agent_id,
                topic_id=agent.topic_id,
                chat_id=agent.chat_id,
                agent_run_id=run.id,
            )
            await live.sdk_session.send(prompt)
        except Exception as exc:
            await self._fail_turn(run.id, exc)

    async def cancel(self, agent_id: int, *, run_id: int | None = None) -> None:
        """Stop an execution. ``run_id`` targets a specific one — the watchdog
        needs that, because a shared agent's *current* run may belong to another
        workflow entirely."""
        run = await self._run(run_id) if run_id is not None else await self._resolve_run(agent_id)
        live = self._live.get(run.id) if run is not None else None
        if live is not None:
            with contextlib.suppress(Exception):
                await live.sdk_session.abort()
            for fut in live.pending.values():
                if not fut.done():
                    fut.set_result(self._reject("cancelled"))
        if run is not None:
            await self._patch_run(run.id, status="cancelled")
        await publish_agent_changed(agent_session_id=agent_id, agent_run_id=run.id if run else None)

    async def resolve_permission(self, agent_id: int, request_id: str, decision: str) -> bool:
        """Resolve a parked permission request. Returns True if one matched."""
        return await self._permissions.resolve(agent_id, request_id, decision)

    async def list_permissions(self) -> list[dict[str, Any]]:
        """Recap of active "approve for session" grants across live sessions."""
        return await self._permissions.list_grants()

    def live_activity(self, agent_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Snapshot each agent's in-flight work for the dashboard cockpit.

        Reads the in-memory event cache (no DB, no ``await``) to derive, per
        agent: the currently running tool, how many tool calls are running in
        parallel (the "sub-agent fan-out" cluster), a plain-language **narration**
        line distilled from the agent's own commentary this turn, and the oldest
        unresolved permission request. Everything resets at turn boundaries so a
        dropped completion event can't leave a tool "running" across turns.
        Agents not live in-process report an empty snapshot.
        """
        out: dict[int, dict[str, Any]] = {}
        for aid in agent_ids:
            running: dict[str, str | None] = {}
            order: list[str] = []
            # Rolling live-narration state, reset at every turn boundary so a
            # resting agent shows nothing and each turn narrates itself.
            narration_msg: str | None = None  # last completed assistant message
            delta_buf: list[str] = []  # deltas since that message (newer text)
            delta_len = 0
            for ev in self._events.get(aid, ()):
                if ev.kind in ("turn_start", "turn_end", "idle", "aborted"):
                    running.clear()
                    order.clear()
                    narration_msg = None
                    delta_buf = []
                    delta_len = 0
                    continue
                if ev.kind == "assistant_message":
                    if ev.text:
                        narration_msg = ev.text
                    delta_buf = []
                    delta_len = 0
                    continue
                if ev.kind == "assistant_delta":
                    # Accumulate just enough of the streaming message to recover
                    # its first line; stop once we clearly have it so a long
                    # answer can't make this scan quadratic across polls.
                    if ev.text and delta_len < 400:
                        delta_buf.append(ev.text)
                        delta_len += len(ev.text)
                    continue
                rid = ev.request_id
                if not rid:
                    continue
                if ev.tool_status == "running":
                    if rid not in running:
                        order.append(rid)
                    running[rid] = ev.tool_name
                elif ev.tool_status in ("done", "error"):
                    running.pop(rid, None)
                    if rid in order:
                        order.remove(rid)
            # In-flight deltas are newer than the last completed message, so
            # prefer them; fall back to the last full message otherwise.
            raw_narration = "".join(delta_buf) if delta_buf else narration_msg
            narration = _clean_narration(raw_narration) if raw_narration else None
            run_id = self._agent_runs.get(aid)
            live = self._live.get(run_id) if run_id is not None else None
            pending: dict[str, Any] | None = None
            if live is not None and live.pending_info:
                info = next(iter(live.pending_info.values()))
                pending = {
                    "request_id": info.get("request_id"),
                    "title": info.get("title"),
                    # The whole payload travels: the workflow board renders the
                    # decision card from it, and it has no event stream to mine.
                    "data": dict(info),
                }
            out[aid] = {
                "active_tool": running.get(order[-1]) if order else None,
                "active_tool_count": len(order),
                "active_narration": narration,
                "pending_permission": pending,
            }
        return out

    async def reset_permissions(self) -> int:
        """Revoke all session grants by disconnecting every live session."""
        return await self._permissions.reset()

    async def get_events(
        self, agent_id: int, *, agent_run_id: int | None = None
    ) -> list[AgentEvent]:
        """The whole transcript: stable history followed by any parked approvals."""
        return await self._transcript.get_events(agent_id, agent_run_id=agent_run_id)

    async def get_events_page(
        self, agent_id: int, *, agent_run_id: int | None = None, after: int = 0
    ) -> AgentEventPage:
        """The transcript from ``after`` onward, for an incremental live reader."""
        return await self._transcript.get_events_page(
            agent_id, agent_run_id=agent_run_id, after=after
        )

    async def _teardown_run(self, run_id: int, *, forget: bool = False) -> None:
        """Disconnect a single run's live SDK session and drop its runtime state.

        ``forget`` additionally clears the run's *agent-level* timeline (memory
        cache and archive). That's deliberately broader than the run — the
        archive is per-agent — and is only used when the agent itself is going
        away or its conversation is being erased wholesale.
        """
        live = self._live.pop(run_id, None)
        self._live_locks.pop(run_id, None)
        if live is not None:
            with contextlib.suppress(Exception):
                await live.sdk_session.disconnect()
        run = await self._run(run_id)
        agent_id = run.agent_id if run is not None else None
        if agent_id is not None and self._agent_runs.get(agent_id) == run_id:
            self._agent_runs.pop(agent_id, None)
        if not forget or agent_id is None:
            return
        self._events.pop(agent_id, None)
        self._loaded.discard(agent_id)
        self._event_locks.pop(agent_id, None)
        self._auth_announced.pop(agent_id, None)
        # SQLite doesn't enforce ON DELETE CASCADE unless the foreign_keys
        # pragma is on, so clear the archive explicitly (the codebase manages
        # such cleanups in the app layer — see roles/topics delete).
        async with SessionLocal() as session:
            await session.execute(
                delete(AgentEventRecord).where(AgentEventRecord.agent_session_id == agent_id)
            )
            await session.commit()

    async def teardown_session(self, agent_id: int, *, forget: bool = False) -> None:
        """Disconnect an agent's live sessions (e.g. before deleting the row).

        The archived timeline is kept by default so linking a topic — which
        recreates the session to re-inject context — doesn't wipe the workflow
        view. Pass ``forget=True`` when the agent is being deleted.

        Tears down *every* run of the agent that is live in-process, not just the
        current one: a second workflow may still be holding a session against the
        same definition, and leaving it connected would outlive the row.
        """
        run_ids: list[int] = []
        async with SessionLocal() as session:
            run_ids = [
                r
                for r in (
                    await session.execute(select(AgentRun.id).where(AgentRun.agent_id == agent_id))
                )
                .scalars()
                .all()
                if r in self._live or r in self._live_locks
            ]
        current = self._agent_runs.get(agent_id)
        if current is not None and current not in run_ids:
            run_ids.append(current)
        for run_id in run_ids:
            await self._teardown_run(run_id)
        if forget:
            self._events.pop(agent_id, None)
            self._loaded.discard(agent_id)
            self._event_locks.pop(agent_id, None)
            self._auth_announced.pop(agent_id, None)
            self._agent_runs.pop(agent_id, None)
            async with SessionLocal() as session:
                await session.execute(
                    delete(AgentEventRecord).where(AgentEventRecord.agent_session_id == agent_id)
                )
                await session.commit()

    # ------------------------------------------------------------------ commands

    async def clear_session(
        self, agent_id: int, *, keep_id: bool = False, trigger: str = "manual"
    ) -> None:
        """Erase an agent's conversation and start its SDK context from scratch.

        Disconnects + forgets the live session and wipes the archived timeline
        (``teardown_session(forget=True)``), then opens a **fresh run** — which
        is what "start from scratch" now means: a new execution row with its own
        status, counters, capability snapshot and (empty) artifact set. The prior
        run stays as audit history.

        ``keep_id`` selects what happens to the SDK handle:

        * ``False`` (default, interactive ``/clear``) starts the new run with no
          handle, so the next turn mints a brand-new conversation.
        * ``True`` carries the previous run's ``copilot_session_id`` over and
          deletes the SDK's on-disk state for it, so a scheduled ``/agent <uuid>``
          reference (which targets the agent's ``public_id``) keeps resolving
          while still getting a clean context on the next turn. Without the
          delete, reusing the id would resume the old transcript from disk and
          defeat the clear.
        """
        prior = await self._resolve_run(agent_id)
        old_id = prior.copilot_session_id if prior is not None else None

        await self.teardown_session(agent_id, forget=True)

        if keep_id and old_id and self._client is not None:
            # Best-effort: a never-connected (pending) agent has nothing on disk.
            with contextlib.suppress(Exception):
                await self._client.delete_session(old_id)

        # A freshened context starts from a clean blackboard too. Artifacts are
        # run-scoped, so the new run is empty by construction; the old run's
        # deliverables are dropped explicitly because `/clear` means "discard
        # that attempt", not "archive it". The trailing `_publish` re-broadcasts
        # `agent.changed`, so the sidebar and in-chat deliverables refetch.
        if prior is not None:
            await self._clear_artifacts(prior.id)

        run = await self._open_run(agent_id, trigger=trigger, inherit_session=keep_id)
        if run is not None:
            await self._patch_run(
                run.id,
                status="idle",
                active_prompt=None,
                result_summary=None,
                error=None,
            )
        await self._publish(agent_id, agent_run_id=run.id if run else None)

    async def rerun_task(
        self, agent_id: int, *, extra: str | None = None, trigger: str = "manual"
    ) -> None:
        """Reset the agent's context (same uuid) and replay its stored task.

        Backs the scheduled ``/agent <uuid> /run`` nudge: instead of the schedule
        re-sending the full instruction block every run (and replaying an
        ever-growing transcript), the instructions live **once** in the agent's
        ``task_prompt``. Each run wipes the prior transcript via
        :meth:`clear_session` (``keep_id=True`` so the schedule's uuid keeps
        resolving), then re-delivers ``task_prompt`` — optionally with an
        ``extra`` one-off note appended for this run — as a clean turn.
        """
        await self.clear_session(agent_id, keep_id=True, trigger=trigger)
        agent = await self._load(agent_id)
        if agent is None:
            return
        prompt = (agent.task_prompt or "").strip()
        if extra:
            extra = extra.strip()
            prompt = f"{prompt}\n\n{extra}" if prompt else extra
        if not prompt:
            return
        await self.send_message(agent_id, prompt)

    async def run_command(self, agent_id: int, name: str, argument: str) -> None:
        """Execute a system slash command for an agent (never forwarded to the SDK).

        Dispatches to a handler from :attr:`_COMMAND_HANDLERS` (rename/clear/
        archive). The visible feedback is the state change itself (header title,
        empty transcript, the session leaving the list). Raises
        :class:`ValueError` for bad usage or an unknown command so the caller can
        surface it. Adding a command is a single registry entry below.
        """
        handler = self._COMMAND_HANDLERS.get(name)
        if handler is None:
            supported = ", ".join(f"/{cmd}" for cmd in self.supported_commands())
            raise ValueError(
                f"/{name} isn't available in agent sessions — only {supported} are supported."
            )
        await handler(self, agent_id, argument)

    # The registry lives in ``commands``; ``supported_commands`` and the
    # frontend's AGENT_SLASH_COMMANDS are derived from its keys.
    _COMMAND_HANDLERS: ClassVar[dict[str, Callable[[AgentManager, int, str], Awaitable[None]]]] = (
        commands.COMMAND_HANDLERS
    )

    @classmethod
    def supported_commands(cls) -> tuple[str, ...]:
        """Names of slash commands an agent session accepts (registry keys)."""
        return tuple(cls._COMMAND_HANDLERS)

    async def _publish(self, agent_id: int, *, agent_run_id: int | None = None) -> None:
        """Emit an ``agent.changed`` signal for an agent by id (loads its links)."""
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
            agent_run_id=agent_run_id
            if agent_run_id is not None
            else (agent.current_run_id if agent else None),
        )

    async def _sanitize_model(self, agent_id: int, model: str) -> str:
        return await self._model_selection.sanitize(agent_id, model)

    async def _block_turn(self, run_id: int, labels: list[str]) -> None:
        """Park this run as ``blocked`` instead of dispatching a tool-less turn.

        Like :meth:`_fail_turn` this lands *outside* the SDK event seam — no turn
        is ever sent, so nothing will emit an event — hence the explicit workflow
        advance. ``blocked`` is what pauses the run (rather than letting the step
        complete on an ``idle`` the agent reached without ever calling a tool),
        and the question tells the user exactly which sign-in unblocks it.

        Scoped to the run: a shared agent's *other* execution may hold a valid
        session for the very server this one is missing, and parking the agent
        would strand it.
        """
        joined = ", ".join(labels)
        question = (
            f"This step needs {joined}, but the sign-in has expired. Re-authenticate, then resume."
        )
        logger.info("agent run %s: blocking turn — no credentials for %s", run_id, joined)
        await self._patch_run(run_id, status="blocked", blocked_question=question, error=None)
        loaded = await self._load_run(run_id)
        if loaded is not None:
            await self._publish(loaded[1].id, agent_run_id=run_id)
        self.enqueue(self._advance_workflows(run_id))

    async def _fail_turn(self, run_id: int, exc: BaseException) -> None:
        """Mark a turn as failed so a dispatch error is visible, not a silent hang.

        Turn dispatch runs as a detached background task (:meth:`enqueue`), so an
        exception there would otherwise be swallowed and leave the agent stuck on
        its "sending…" spinner. Record it as a ``failed`` status with the error
        text and publish, mirroring how the SDK's own ``ErrorData`` is surfaced.

        This lands *outside* the event seam, so the workflow advance is enqueued
        here explicitly: a step whose dispatch blew up emits no further events,
        and without this its run would sit in ``running`` until the watchdog (if
        one is configured at all) noticed.
        """
        logger.exception("agent run %s: turn dispatch failed", run_id)
        message = str(exc).strip() or exc.__class__.__name__
        with contextlib.suppress(Exception):
            await self._patch_run(run_id, status="failed", error=message[:2000])
            loaded = await self._load_run(run_id)
            if loaded is not None:
                await self._publish(loaded[1].id, agent_run_id=run_id)
            self.enqueue(self._advance_workflows(run_id))

    async def list_models(self) -> list[dict[str, Any]]:
        """Return the runtime's available models, or empty."""
        return await self._model_selection.list_models()

    async def apply_session_overrides(self) -> None:
        """Apply the current global model prefs onto idle live sessions."""
        await self._model_selection.apply_session_overrides()

    # Aliased so the broker's calls through the manager honour a patched decision.
    _approval_policy = staticmethod(permissions.approval_policy)
    _approve_once = staticmethod(permissions.approve_once)
    _reject = staticmethod(permissions.reject)
    _decision = staticmethod(permissions.decision)

    # ------------------------------------------------------------------ events

    async def _handle_event(self, run_id: int, event: Any) -> None:
        # Events arrive keyed by the *run* that produced them, but are serialised
        # per *agent*: the idle handler must run after the assistant-message
        # handler has committed ``result_summary`` (otherwise ``_notify_back``
        # posts the previous turn's answer), and ``_notify_back`` writes into the
        # agent's shared topic/chat, so concurrent runs of one agent must take
        # turns even though their sessions are independent.
        loaded = await self._load_run(run_id)
        if loaded is None:
            return
        lock = self._event_locks.setdefault(loaded[1].id, asyncio.Lock())
        async with lock:
            await self._handle_event_locked(run_id, event)

    async def _handle_event_locked(self, run_id: int, event: Any) -> None:
        loaded = await self._load_run(run_id)
        if loaded is None:
            return
        run, agent = loaded
        agent_id = agent.id
        # Archive every event so the timeline persists across session teardown
        # (e.g. on topic link) and process restart, where the SDK would otherwise
        # drop it (``get_events`` only replays ``SessionStartData`` on resume).
        await self._transcript.ensure_loaded(agent_id)
        # The status *before* this event is handled, read up front rather than
        # around the final patch below: handlers reached from here (``_on_idle``,
        # and ``UsageMeter.enforce_budget`` via ``_record_usage``) commit status changes of
        # their own mid-flight, and those are transitions the workflow seam must
        # still see. Read from the *run* — a sibling execution's status must not
        # look like this one transitioning.
        before_status = run.status
        normalised = normalize_event(event)
        normalised.at = datetime.now(UTC)
        # Stamp the producing run so the timeline can be split per execution.
        normalised.agent_run_id = run_id
        self._events.setdefault(agent_id, []).append(normalised)
        await timeline.archive_event(agent_id, normalised, agent_run_id=run_id)

        # A workiq tool that errors after the session was built with valid creds
        # usually means the OAuth token lapsed mid-turn. Surface the same sign-in
        # prompt as the pre-flight gate so the user can re-authenticate inline
        # instead of reading a raw tool failure. Best-effort: only fires when the
        # event carries a server name and the creds are actually gone.
        auth_server = await self._auth_server_from_failed_tool(normalised)
        if auth_server is not None:
            await self._announce_auth_required(agent_id, [auth_server])

        data = getattr(event, "data", event)
        name = type(data).__name__
        now = datetime.now(UTC)
        patch: dict[str, Any] = {"last_activity_at": now}

        if name == "SessionStartData":
            # The only place the SDK hands us the conversation's resume handle.
            # Persisting it is what lets a later session rebuild (an OAuth
            # refresh, a changed MCP catalogue, a recovered sign-in) resume this
            # same conversation instead of silently starting an empty one and
            # dropping the agent's task prompt and history.
            sid = getattr(data, "session_id", None)
            if sid and not run.copilot_session_id:
                await self._patch_run(run_id, copilot_session_id=str(sid))
        elif name == "AssistantMessageData":
            content = getattr(data, "content", None)
            if content:
                # Scrub control directives from the *displayed* summary; keep the
                # raw message in ``pending_answer`` for the topic repost and for
                # directive/gate parsing downstream.
                patch["result_summary"] = strip_control_directives(str(content))[
                    :RESULT_SUMMARY_CAP
                ]
                # Keep the full answer for the topic/chat repost — the summary
                # column is capped for the agent list.
                live = self._live.get(run_id)
                if live is not None:
                    live.pending_answer = str(content)
        elif name == "AssistantUsageData":
            await self._record_usage(run_id, data)
        elif name in ("SessionIdleData", "SystemNotificationAgentIdle"):
            fresh = await self._run(run_id)
            # Don't let a trailing idle event mask a turn that just errored, was
            # paused/cancelled, or already reached a terminal/blocked resting
            # state — those statuses are sticky so the outcome stays visible (and
            # any in-flight prompt stays resumable).
            if fresh is not None and fresh.status not in (
                "needs_approval",
                "cancelled",
                "failed",
                "blocked",
                "completed",
            ):
                # The goal loop decides the resting status (idle/blocked/completed)
                # or continues autonomously; it mutates ``patch`` and reposts only
                # at rest transitions.
                await self._on_idle(agent, fresh, patch)
        elif name in ("AbortData",):
            patch["status"] = "cancelled"
            patch["finished_at"] = datetime.now(UTC)
        elif name in ("ErrorData", "SessionErrorData"):
            patch["status"] = "failed"
            patch["error"] = str(getattr(data, "message", name))[:2000]
            patch["finished_at"] = datetime.now(UTC)
            # Auto-recovery: if a retry budget remains, arm a backoff re-run the
            # scheduler will pick up. Keeps a flaky turn from parking the fleet.
            # The budget is cumulative governance and lives on the definition.
            failed_agent = await self._load(agent_id)
            if failed_agent is not None and failed_agent.retry_count < failed_agent.max_retries:
                patch["next_retry_at"] = self._retry_due_at(failed_agent.retry_count)

        await self._patch_run(run_id, **patch)
        after = await self._run(run_id)
        # Re-read the definition: ``_patch_run`` expired it, and the notification
        # needs its current topic/chat routing.
        owner = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=owner.topic_id if owner else None,
            chat_id=owner.chat_id if owner else None,
            agent_run_id=run_id,
        )
        # Workflow coordination advances when this run *transitions into* a
        # resting/terminal state — a workflow chains plain agents itself. Testing
        # the status alone would re-fire for every subsequent event an already
        # resting agent emits (pending-message, MCP-status and tool-list updates
        # all arrive after a turn ends), and each of those advances raced the
        # others into re-entering the same step: duplicate trace rows, a second
        # real ``start_task``, and double-counted tokens.
        if (
            after is not None
            and after.status != before_status
            and after.status in _RESTING_STATUSES
        ):
            self.enqueue(self._advance_workflows(run_id))

    async def _record_usage(self, run_id: int, data: Any) -> None:
        await self._usage.record(run_id, data)

    async def _agent_spend(self, agent_id: int) -> int:
        return await usage.agent_spend(agent_id)

    async def _on_idle(self, agent: AgentSession, run: AgentRun, patch: dict[str, Any]) -> None:
        """Resolve what a finished turn means for an agent's mission.

        This is the goal loop. For a **plain** agent it just rests at ``idle`` and
        reposts the exchange (unchanged behaviour). For an **autonomous** agent it
        reads the control directives the model embedded in its last message and:

        * ``OBJECTIVE_COMPLETE`` → terminal ``completed`` (one final repost);
        * ``NEED_INPUT`` → ``blocked`` on the raised question (repost);
        * otherwise, if the step budget remains and it isn't stalling, it keeps
          going — status stays ``running``, the step count ticks up, and the next
          step is enqueued with **no** repost (so a whole multi-step mission lands
          as a single objective→result exchange when it finally rests);
        * budget exhausted or stalled → ``blocked`` for a human to course-correct.

        Progress (``PROGRESS: n | label``) is applied whenever present and resets
        the stall counter on a fresh value. Mutates ``patch`` in place.

        Step count and progress are read from ``run``: each execution pursues the
        objective on its own budget, so a sibling run can't spend this one's steps.
        """
        live = self._live.get(run.id)
        directives = (
            parse_agent_directives(live.pending_answer)
            if live is not None and agent.autonomy_enabled
            else {}
        )
        if live is not None:
            live.directive = directives or None

        # A progress report applies regardless of the terminal decision below, and
        # a *new* value clears the stall counter (the agent is genuinely moving).
        progress = directives.get("progress")
        if progress is not None:
            patch["progress"] = progress["value"]
            patch["progress_label"] = progress["label"]
            if live is not None and progress["value"] != live.last_progress:
                live.last_progress = progress["value"]
                live.stall_count = 0

        # Published artifacts land on the blackboard regardless of the terminal
        # decision below, so mid-mission outputs are shared as soon as they're
        # emitted (not only at completion).
        artifacts = directives.get("artifacts")
        if artifacts:
            await self._persist_artifacts(run.id, artifacts, kind="output")

        # 1) Objective met — terminal. The single repost carries the summary.
        if directives.get("complete"):
            # The OBJECTIVE_COMPLETE reason is a *meta* description of the work
            # ("Told the user a joke"); the actual deliverable is the message's
            # prose — the joke itself. Prefer that prose as the displayed result,
            # falling back to the reason only when the final turn was
            # directives-only (a bare OBJECTIVE_COMPLETE with no body).
            reason = strip_control_directives(str(directives["complete"]))[:RESULT_SUMMARY_CAP]
            body = (
                strip_control_directives(str(live.pending_answer))[:RESULT_SUMMARY_CAP]
                if live is not None and live.pending_answer
                else ""
            )
            summary = body or reason
            patch["status"] = "completed"
            patch["result_summary"] = summary
            patch["active_prompt"] = None
            patch["progress"] = 100
            patch["step_count"] = 0
            patch["finished_at"] = datetime.now(UTC)
            if live is not None:
                live.stall_count = 0
            # Auto-capture the outcome as a result artifact so downstream agents
            # get the deliverable even when the model didn't emit an ARTIFACT line.
            if summary:
                await self._persist_artifacts(
                    run.id, [{"title": "Result", "content": summary}], kind="result"
                )
            await self._notify_back(agent, run)
            return

        # 2) Agent raised a decision only the human can make — park it.
        if directives.get("blocked"):
            patch["status"] = "blocked"
            patch["blocked_question"] = str(directives["blocked"])[:2000]
            patch["active_prompt"] = None
            await self._notify_back(agent, run)
            return

        # 3) Autonomous continuation — keep pursuing the objective if allowed.
        if agent.autonomy_enabled:
            if live is not None and live.stall_count >= _STALL_LIMIT:
                patch["status"] = "blocked"
                patch["blocked_question"] = (
                    "I've taken several steps without measurable progress toward "
                    "the objective. Please review and advise on how to proceed."
                )
                patch["active_prompt"] = None
                await self._notify_back(agent, run)
                return
            if run.step_count < agent.max_steps:
                # Keep going. No repost — the mission is still in flight; the
                # single objective→result exchange lands when it rests.
                patch["status"] = "running"
                patch["step_count"] = run.step_count + 1
                # No progress advance this step counts toward a stall.
                if live is not None and progress is None:
                    live.stall_count += 1
                self.enqueue(self._advance_goal_loop(run.id))
                return
            # Budget spent without completing — hand back rather than run forever.
            patch["status"] = "blocked"
            patch["blocked_question"] = (
                f"I've reached the step budget ({agent.max_steps} steps) for this "
                "objective without completing it. Review my progress and tell me "
                "whether to continue, adjust the objective, or stop."
            )
            patch["active_prompt"] = None
            await self._notify_back(agent, run)
            return

        # 4) Plain agent: rest at idle and repost this turn's exchange.
        patch["status"] = "idle"
        patch["active_prompt"] = None
        await self._notify_back(agent, run)

    async def _advance_goal_loop(self, run_id: int) -> None:
        """Take the next autonomous step toward the objective.

        Enqueued (never called inline) from the idle handler — we must not
        ``send`` from inside the locked event handler. Reloads the run,
        re-checks it's still an autonomous execution that should continue,
        refreshes the per-turn approval policy, and nudges the SDK session to keep
        working. ``pending_prompt`` is deliberately left untouched so the eventual
        single repost still shows the objective + final answer.
        """
        agent_id: int | None = None
        try:
            loaded = await self._load_run(run_id)
            if loaded is None:
                return
            run, agent = loaded
            agent_id = agent.id
            if not agent.autonomy_enabled or run.status != "running":
                return
            live = self._live.get(run_id)
            if live is None:
                return
            live.approval_policy = await self._approval_policy(agent, run)
            # Reset the per-turn answer buffer so the next directive parse reads
            # only the upcoming step's message.
            live.pending_answer = None
            await live.sdk_session.send(_CONTINUE_NUDGE)
        except Exception as exc:
            if agent_id is not None:
                await self._fail_turn(run_id, exc)

    async def _notify_back(self, agent: AgentSession, run: AgentRun) -> None:
        """Post the just-finished turn's exchange into the linked container.

        Posts the turn's **prompt** (as a user turn) and the agent's **answer**
        (as an assistant turn), both tagged with ``agent_session_id`` so the UI
        renders an "agent exchange" badge linking back to ``/agents/{id}``. Like
        the reminder ticker, the discussion goes unread + notifies.

        Posts **once per turn**: the prompt is captured on ``_LiveSession`` when a
        task/follow-up is sent and cleared here, so repeated idle events for the
        same turn don't double-post and every turn (not just the first) lands in
        the topic. A resumed turn with no tracked prompt is skipped.
        """
        if agent.topic_id is None and agent.chat_id is None:
            return

        live = self._live.get(run.id)
        if live is None or live.pending_prompt is None:
            return
        prompt = live.pending_prompt
        live.pending_prompt = None

        # Prefer the full assistant text captured this turn; fall back to the
        # (capped) summary so a resumed turn without a tracked answer still posts.
        answer = (live.pending_answer or run.result_summary or "").strip() or "Agent task finished."
        live.pending_answer = None
        answer, suggestions = split_suggestions(answer)
        now = datetime.now(UTC)
        # Keep the posted messages strictly newer than any last_read_at we pin,
        # so the unread badge lights up reliably (mirrors the reminder ticker).
        read_threshold = now - timedelta(seconds=1)
        async with SessionLocal() as session:
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.USER,
                    content=prompt,
                    agent_session_id=agent.id,
                    created_at=now,
                )
            )
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.ASSISTANT,
                    content=answer,
                    suggestions=json.dumps(suggestions) if suggestions else None,
                    agent_session_id=agent.id,
                    created_at=now,
                )
            )
            # Ensure the linked conversation reads as unread even when it was
            # never opened: last_read_at IS NULL is treated as fully read, so the
            # agent's reply wouldn't count. Pin last_read just before the messages
            # when null (or somehow stamped in the future) without masking other
            # genuinely-unread history. Mirrors services/reminders.py.
            container: Topic | Chat | None = None
            if agent.topic_id is not None:
                container = await session.get(Topic, agent.topic_id)
            elif agent.chat_id is not None:
                container = await session.get(Chat, agent.chat_id)
            if container is not None:
                last_read = container.last_read_at
                if last_read is not None and last_read.tzinfo is None:
                    last_read = last_read.replace(tzinfo=UTC)
                if last_read is None or last_read > read_threshold:
                    container.last_read_at = read_threshold
            await session.commit()
        if agent.topic_id is not None:
            await publish_message_changed(agent.topic_id)
        elif agent.chat_id is not None:
            await publish_message_changed_chat(agent.chat_id)

    # ------------------------------------------------------------------ DB utils

    async def _load(self, agent_id: int) -> AgentSession | None:
        async with SessionLocal() as session:
            return await session.get(AgentSession, agent_id)

    async def _patch(self, agent_id: int, **values: Any) -> None:
        if not values:
            return
        async with SessionLocal() as session:
            agent = await session.get(AgentSession, agent_id)
            if agent is None:
                return
            for key, value in values.items():
                setattr(agent, key, value)
            await session.commit()

    # ---------------------------------------------------------------- fleet ----

    async def _persist_artifacts(
        self, run_id: int, artifacts: list[dict[str, str]], *, kind: str
    ) -> None:
        await self._artifacts.persist(run_id, artifacts, kind=kind)

    async def _clear_artifacts(self, run_id: int) -> None:
        await clear_artifacts(run_id)

    def _retry_due_at(self, retry_count: int) -> datetime:
        """Next-attempt time with exponential backoff off the base interval."""
        settings = get_settings()
        base = max(1, settings.agents_retry_backoff_seconds)
        delay = base * (2**retry_count)
        return datetime.now(UTC) + timedelta(seconds=delay)

    async def _advance_workflows(self, run_id: int) -> None:
        """Advance the workflow this run belongs to, if any.

        Enqueued from the completion seam when a run reaches a resting or
        terminal state. Delegates to the workflow coordinator (imported lazily to
        avoid a circular import) in a fresh session so the advance commits
        independently of the turn that triggered it. Because a run belongs to at
        most one workflow run, a shared agent no longer fans an advance out to
        every workflow pointing at it.
        """
        try:
            from precursor.backend.services.agents import workflow as workflow_svc

            async with SessionLocal() as session:
                await workflow_svc.advance_for_run(session, self, run_id)
        except Exception:
            logger.debug("failed to advance workflows after run %s", run_id, exc_info=True)

    async def release_ready_fleet(self) -> None:
        """Sweep for orphaned ``pending`` agents and start them.

        ``pending`` is a brief transient: :func:`_spawn_agent` creates an agent
        ``pending`` and enqueues its ``start_task``. This backstop starts any
        ``pending`` agent whose ``start_task`` was lost (e.g. the app restarted
        mid-spawn), respecting the concurrency governor. ``waiting`` agents are
        parked deliberately for a manual/webhook trigger and are never swept.
        """
        if not self.ready:
            return
        try:
            settings = get_settings()
            async with SessionLocal() as session:
                rows = await session.execute(
                    select(AgentSession.id).where(
                        AgentSession.status == "pending",
                        AgentSession.archived_at.is_(None),
                    )
                )
                candidates = [int(r) for r in rows.scalars().all()]
            for agent_id in candidates:
                async with SessionLocal() as session:
                    if await fleet.running_count(session) >= settings.agents_max_concurrent:
                        break
                await self.start_task(agent_id, trigger="fleet")
        except Exception:
            logger.debug("fleet sweep failed", exc_info=True)

    async def retry_agent(self, agent_id: int) -> None:
        """Re-run a failed agent, counting the attempt against its retry budget.

        Invoked by the scheduler when ``next_retry_at`` comes due. Clears the
        retry arming and error, bumps ``retry_count``, then replays the task.
        """
        agent = await self._load(agent_id)
        if agent is None or agent.status != "failed":
            return
        if agent.retry_count >= agent.max_retries:
            return
        await self._patch(
            agent_id,
            retry_count=agent.retry_count + 1,
            next_retry_at=None,
            error=None,
        )
        await self.restart_with_task(agent_id, trigger="retry")


# Map SDK event class names → coarse workflow step kinds for the UI.
_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
