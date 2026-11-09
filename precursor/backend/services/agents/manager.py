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
(``permissions``), slash commands and session commands (``commands``), the
event timeline and live activity (``timeline``), model selection (``models``),
token metering (``usage``), the artifact blackboard (``artifacts``), the system
preamble (``prompting``), run persistence (``runs``), live SDK sessions
(``session_pool``), turn dispatch (``turns``), SDK event handling
(``event_handling``), the autonomy goal loop (``goal_loop``) and the watchdog
(``watchdog``). The manager keeps a same-named delegate for each moved method,
so callers and tests that reach ``manager._x`` (or patch it) still work.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentRun,
    AgentSession,
)
from precursor.backend.schemas.agent import (
    AgentEvent,
    AgentEventPage,
    AgentRewindMode,
    AgentRewindPreview,
    AgentRewindResult,
)
from precursor.backend.services.agents import (
    commands,
    fleet,
    mcp_config,
    permissions,
    runtime,
    usage,
)
from precursor.backend.services.agents import rewind as rewind_mod
from precursor.backend.services.agents.artifacts import ArtifactStore, clear_artifacts
from precursor.backend.services.agents.directives import (
    RESULT_SUMMARY_CAP,
    parse_agent_command,
    parse_agent_directives,
    strip_control_directives,
)
from precursor.backend.services.agents.event_handling import EventHandler
from precursor.backend.services.agents.goal_loop import GoalLoop
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
from precursor.backend.services.agents.runs import RunStore
from precursor.backend.services.agents.sdk_logging import _quiet_sdk_teardown_pipe_noise
from precursor.backend.services.agents.session_pool import SessionPool
from precursor.backend.services.agents.timeline import Timeline
from precursor.backend.services.agents.turns import TurnDispatcher
from precursor.backend.services.agents.usage import UsageMeter
from precursor.backend.services.agents.watchdog import Watchdog
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.events import (
    publish_agent_changed,
    set_current_client_id,
)

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


# Cap how long we wait for the out-of-process runtime to come up so a stuck or
# unauthenticated CLI can't block app startup or a settings save indefinitely.
_START_TIMEOUT_SECONDS = 30.0


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
        # Per-agent count of archive truncations (rewinds, clears), and a nonce
        # for this process, together naming the archive a reader's cursor counts
        # into (see ``Timeline.epoch``).
        self._transcript_epochs: dict[int, int] = {}
        self._epoch_nonce = os.urandom(4).hex()
        # Per-agent locks serialising event handling so SDK events are processed
        # in arrival order — otherwise an idle handler can race ahead of the
        # assistant-message handler and post a stale answer back to the topic.
        # Per *agent* rather than per run on purpose: ``_notify_back`` posts into
        # the agent's shared topic/chat, so concurrent runs must still take turns.
        self._event_locks: dict[int, asyncio.Lock] = {}
        # The lock alone can't keep that order: a handler awaits a DB read before
        # it knows which lock to take, so events racing in a burst reach the lock
        # shuffled. SDK events are therefore queued per agent, in the order the
        # SDK delivers them, and a single drainer task per agent works through
        # each queue. See ``_dispatch_sdk_event``.
        self._event_queues: dict[int, deque[tuple[int, Any]]] = {}
        self._event_drainers: set[int] = set()
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
        self._runs = RunStore(self)
        self._sessions = SessionPool(self)
        self._turns = TurnDispatcher(self)
        self._event_handler = EventHandler(self)
        self._goal_loop = GoalLoop(self)
        self._watchdog = Watchdog(self)

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
            if not await self._start_client():
                return
            self._ready = True
            logger.info("Agents runtime started (%s).", detail)
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog.loop())

    async def _start_client(self) -> bool:
        """Spawn the CLI server and connect to it; ``False`` if it won't come up."""
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
            return False
        return True

    def _runtime_alive(self) -> bool:
        """Whether the CLI server behind the client is still there.

        The SDK has no public health accessor. When the CLI exits (e.g. a V8 heap
        OOM) its reader thread flips ``_state`` to ``disconnected`` but nothing
        else reacts: every later request writes to the dead child's stdin and
        fails with ``[Errno 32] Broken pipe`` until the app is restarted.
        """
        if self._client is None:
            return False
        return getattr(self._client, "_state", None) not in {"disconnected", "error"}

    async def _ensure_runtime(self) -> None:
        """Restart the CLI server if it died while the app kept running."""
        if not self._ready or self._runtime_alive():
            return
        async with self._lock:
            if not self._ready or self._runtime_alive():
                return
            logger.warning("Copilot runtime exited unexpectedly; restarting it")
            # Its sessions died with it. A turn that was in flight is left to the
            # watchdog, which interrupts it so it can be resumed.
            self._live.clear()
            with _quiet_sdk_teardown_pipe_noise(), contextlib.suppress(Exception):
                if self._client is not None:
                    await self._client.force_stop()
            self._client = None
            if await self._start_client():
                logger.info("Copilot runtime restarted.")

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
        await self._watchdog.mark_interrupted_on_boot()

    # ------------------------------------------------------------------ watchdog

    async def _release_idle_sessions(self) -> None:
        await self._watchdog.release_idle_sessions()

    async def _watchdog_sweep(self) -> None:
        await self._watchdog.sweep()

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
        return await self._runs.open_run(
            agent_id,
            trigger=trigger,
            workflow_run_id=workflow_run_id,
            workflow_run_step_id=workflow_run_step_id,
            inherit_session=inherit_session,
        )

    async def _resolve_run(self, agent_id: int) -> AgentRun | None:
        return await self._runs.resolve_run(agent_id)

    async def _run(self, run_id: int) -> AgentRun | None:
        return await self._runs.get_run(run_id)

    async def _load_run(self, run_id: int) -> tuple[AgentRun, AgentSession] | None:
        return await self._runs.load_run(run_id)

    async def _patch_run(self, run_id: int, **values: Any) -> None:
        await self._runs.patch_run(run_id, **values)

    # ------------------------------------------------------------------ sessions

    async def _ensure_live(self, agent: AgentSession, run: AgentRun) -> _LiveSession:
        return await self._sessions.ensure_live(agent, run)

    async def _ensure_live_locked(self, agent: AgentSession, run: AgentRun) -> _LiveSession:
        return await self._sessions.ensure_live_locked(agent, run)

    async def _emit_synthetic(self, agent_id: int, event: AgentEvent) -> None:
        await self._sessions.emit_synthetic(agent_id, event)

    async def _announce_auth_required(self, agent_id: int, servers: list[str]) -> None:
        await self._mcp.announce_auth_required(agent_id, servers)

    async def refresh_oauth_sessions(self) -> None:
        """Drop idle live sessions after an interactive MCP sign-in."""
        await self._sessions.refresh_oauth_sessions()

    async def start_task(
        self,
        agent_id: int,
        extra_context: str | None = None,
        *,
        run_id: int | None = None,
        trigger: str = "manual",
    ) -> None:
        """Begin a fresh execution of the agent's objective."""
        await self._turns.start_task(agent_id, extra_context, run_id=run_id, trigger=trigger)

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
        await self._turns.send_message(agent_id, text)

    async def resume(self, agent_id: int) -> None:
        """Re-run the in-flight turn of an interrupted session."""
        await self._turns.resume(agent_id)

    async def cancel(self, agent_id: int, *, run_id: int | None = None) -> None:
        """Stop an execution; ``run_id`` targets one (see the turn dispatcher)."""
        await self._turns.cancel(agent_id, run_id=run_id)

    async def resolve_permission(self, agent_id: int, request_id: str, decision: str) -> bool:
        """Resolve a parked permission request. Returns True if one matched."""
        return await self._permissions.resolve(agent_id, request_id, decision)

    async def list_permissions(self) -> list[dict[str, Any]]:
        """Recap of active "approve for session" grants across live sessions."""
        return await self._permissions.list_grants()

    def live_activity(self, agent_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Snapshot each agent's in-flight work for the dashboard cockpit."""
        return self._transcript.live_activity(agent_ids)

    async def reset_permissions(self) -> int:
        """Revoke all session grants by disconnecting every live session."""
        return await self._permissions.reset()

    async def get_events(
        self, agent_id: int, *, agent_run_id: int | None = None
    ) -> list[AgentEvent]:
        """The whole transcript: stable history followed by any parked approvals."""
        return await self._transcript.get_events(agent_id, agent_run_id=agent_run_id)

    async def get_events_page(
        self,
        agent_id: int,
        *,
        agent_run_id: int | None = None,
        after: int = 0,
        epoch: str | None = None,
    ) -> AgentEventPage:
        """The transcript from ``after`` onward, for an incremental live reader."""
        return await self._transcript.get_events_page(
            agent_id, agent_run_id=agent_run_id, after=after, epoch=epoch
        )

    async def _teardown_run(self, run_id: int, *, forget: bool = False) -> None:
        await self._sessions.teardown_run(run_id, forget=forget)

    async def teardown_session(self, agent_id: int, *, forget: bool = False) -> None:
        """Disconnect an agent's live sessions (e.g. before deleting the row)."""
        await self._sessions.teardown_session(agent_id, forget=forget)

    # ------------------------------------------------------------------ commands

    async def clear_session(
        self, agent_id: int, *, keep_id: bool = False, trigger: str = "manual"
    ) -> None:
        """Erase an agent's conversation and start its SDK context from scratch."""
        await commands.clear_session(self, agent_id, keep_id=keep_id, trigger=trigger)

    async def compact(self, agent_id: int, instructions: str = "") -> None:
        """Summarise the agent's conversation to free context (``/compact``)."""
        await commands.compact(self, agent_id, instructions)

    async def rewind(
        self, agent_id: int, event_id: str, mode: AgentRewindMode = "conversation"
    ) -> AgentRewindResult:
        """Drop a prompt of the current session and everything after it.

        See :mod:`.rewind`. Raises :class:`~.rewind.RewindError` with a
        user-facing reason and its HTTP status.
        """
        return await rewind_mod.rewind(self, agent_id, event_id, mode)

    async def preview_rewind(self, agent_id: int, event_id: str) -> AgentRewindPreview:
        """What rewinding to ``event_id`` would restore on disk (see :mod:`.rewind`)."""
        return await rewind_mod.preview(self, agent_id, event_id)

    async def rerun_task(
        self, agent_id: int, *, extra: str | None = None, trigger: str = "manual"
    ) -> None:
        """Reset the agent's context (same uuid) and replay its stored task."""
        await commands.rerun_task(self, agent_id, extra=extra, trigger=trigger)

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
        await self._turns.block_turn(run_id, labels)

    async def _fail_turn(self, run_id: int, exc: BaseException) -> None:
        await self._turns.fail_turn(run_id, exc)

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

    def _dispatch_sdk_event(self, agent_id: int, run_id: int, event: Any) -> None:
        self._event_handler.dispatch(agent_id, run_id, event)

    async def _handle_event_locked(self, run_id: int, event: Any) -> None:
        await self._event_handler.handle_locked(run_id, event)

    async def _record(self, agent_id: int, event: AgentEvent) -> None:
        await self._event_handler.record(agent_id, event)

    async def _record_usage(self, run_id: int, data: Any) -> None:
        await self._usage.record(run_id, data)

    async def _agent_spend(self, agent_id: int) -> int:
        return await usage.agent_spend(agent_id)

    async def _on_idle(self, agent: AgentSession, run: AgentRun, patch: dict[str, Any]) -> None:
        await self._goal_loop.on_idle(agent, run, patch)

    async def _notify_back(self, agent: AgentSession, run: AgentRun) -> None:
        await self._goal_loop.notify_back(agent, run)

    # ------------------------------------------------------------------ DB utils

    async def _load(self, agent_id: int) -> AgentSession | None:
        return await self._runs.load_agent(agent_id)

    async def _patch(self, agent_id: int, **values: Any) -> None:
        await self._runs.patch_agent(agent_id, **values)

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


_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
