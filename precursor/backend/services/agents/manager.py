"""AgentManager — owns the Copilot SDK runtime and live agent sessions.

This is the bridge between Precursor's thin ``AgentSession`` rows and the Copilot
SDK's out-of-process agent runtime. One ``CopilotClient`` (one CLI server) is
started for the app's lifetime; each Precursor agent maps to one **persistent**
SDK session (keyed by ``copilot_session_id``, state stored under
``agents_home``), so sessions survive restarts and can be resumed.

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
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import delete, select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentEventRecord,
    AgentSession,
    Chat,
    Message,
    MessageRole,
    Topic,
)
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents import runtime
from precursor.backend.services.app_settings import (
    resolve_agents_approval_policy,
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_enabled,
    resolve_agents_reasoning_effort,
    resolve_agents_system_prompt,
    resolve_agents_watchdog_timeout,
)
from precursor.backend.services.events import (
    publish_agent_changed,
    publish_message_changed,
    publish_message_changed_chat,
    set_current_client_id,
)
from precursor.backend.services.memories import build_memory_prompt
from precursor.backend.services.suggestions import (
    SUGGESTIONS_INSTRUCTION,
    split_suggestions,
)
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

# Slash commands the system intercepts inside an agent session map to real actions
# (rename/clear/archive) handled in ``AgentManager.run_command`` rather than being
# forwarded to the SDK as prompt text. Every *other* slash command is rejected.
_SLASH_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9-]*)\s*([\s\S]*)$")


def parse_agent_command(message: str) -> tuple[str, str] | None:
    """Recognise a leading slash command in a message sent to an agent.

    Returns ``(name, argument)`` for *any* ``/word …`` input (so the caller can
    reject unknown commands instead of leaking them to the SDK), or ``None`` when
    the text is a normal message.
    """
    text = message.lstrip()
    if not text.startswith("/"):
        return None
    match = _SLASH_RE.match(text)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip()


# Cap how long we wait for the out-of-process runtime to come up so a stuck or
# unauthenticated CLI can't block app startup or a settings save indefinitely.
_START_TIMEOUT_SECONDS = 30.0

# How often the watchdog sweeps for stalled running sessions.
_WATCHDOG_INTERVAL_SECONDS = 60.0

# Long-lived agent SDK sessions bake an OAuth bearer header in at create time
# (the SDK can't refresh a static header). We rebuild the session a little before
# the token actually expires so a transparent re-mint never races a live call.
_OAUTH_REFRESH_MARGIN = timedelta(minutes=5)

# Conservative time-to-live when a token's real expiry can't be determined
# (legacy token saved before we stamped issue time, or no ``expires_in``).
_OAUTH_FALLBACK_TTL = timedelta(minutes=30)

# Cap the tool result/error text we archive per event. Tool output (e.g. a
# fetched page) can be huge; the timeline only needs enough to show "what was
# done / why it failed", and the model already got the full payload live.
_TOOL_RESULT_CAP = 4000


@dataclass
class _LiveSession:
    """A live SDK session handle plus its pending permission requests."""

    sdk_session: Any
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    # request_id -> normalised description of what's being requested, so the UI
    # can render an inline approval card explaining the action.
    pending_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    # "Approve for session" grants made during this live session's lifetime, kept
    # so Settings can recap and revoke them. Session-scoped on purpose: these
    # mirror the SDK's per-session approvals and reset when the session does.
    grants: list[dict[str, Any]] = field(default_factory=list)
    # Signatures (type, target) the user approved "for the session". We enforce
    # session scope ourselves — auto-approving matching requests — rather than
    # returning the SDK's approve-for-session decision, whose ``approval`` object
    # is mandatory for command/write prompts and easy to get wrong.
    session_approvals: set[tuple[str, str | None]] = field(default_factory=set)
    # Approval policy resolved once per turn (in ``start_task``/``send_message``)
    # and read by the permission handler. We deliberately do NOT hit the DB from
    # inside the SDK's permission callback — under concurrent writes a transient
    # SQLite lock there would otherwise raise and the SDK turns a raising handler
    # into an opaque, detail-less denial (even in autonomous mode).
    approval_policy: str | None = None
    # The prompt for the turn currently in flight, set when we send a task or a
    # follow-up and cleared once posted to the linked container. Lets us post
    # *every* turn's exchange to the topic/chat (not just the first), keyed to
    # the right prompt rather than always the initial ``task_prompt``.
    pending_prompt: str | None = None
    # Full text of the most recent assistant message for the in-flight turn.
    # ``result_summary`` is capped for the agent list, so we keep the untruncated
    # answer here to repost the complete exchange into the linked topic/chat.
    pending_answer: str | None = None
    # Soonest expiry across any OAuth-protected MCP server attached to this SDK
    # session (today only WorkIQ preview). The bearer header is static, so once
    # this passes we rebuild the session to re-mint it. ``None`` means nothing
    # attached needs refreshing.
    oauth_expires_at: datetime | None = None
    # Set of enabled+registered catalog server names this session was built with
    # (see ``_enabled_catalog_fingerprint``). MCP servers are wired at build time
    # only, so we snapshot the effective set here and rebuild the session when it
    # changes — otherwise a server toggled on in Settings after the session was
    # built stays invisible to the agent until a restart. ``None`` means we didn't
    # attach a catalog (SDK unavailable) and should never rebuild on this basis.
    mcp_fingerprint: frozenset[str] | None = None


class AgentManager:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._ready = False
        self._live: dict[int, _LiveSession] = {}
        # Durable per-agent timeline. The SDK's ``get_events`` is per-connection
        # (a resumed session only replays ``SessionStartData``), so we archive
        # every streamed event. This in-memory copy is a write-through cache over
        # the ``agent_events`` table: it survives ``teardown_session`` (e.g. on
        # topic link) and, because every event is also persisted, the timeline is
        # reloaded from the DB after a process restart (see ``_ensure_loaded``).
        # Cleared only when the agent is deleted.
        self._events: dict[int, list[AgentEvent]] = {}
        # Agents whose DB archive has been hydrated into ``_events`` this process.
        self._loaded: set[int] = set()
        self._events_lock = asyncio.Lock()
        # Per-agent locks serialising event handling so SDK events are processed
        # in arrival order — otherwise an idle handler can race ahead of the
        # assistant-message handler and post a stale answer back to the topic.
        self._event_locks: dict[int, asyncio.Lock] = {}
        # Per-agent set of OAuth servers we've already surfaced a sign-in prompt
        # for, so a held session doesn't re-announce ``mcp_auth_required`` on
        # every rebuild/tool error. Cleared once the server attaches with valid
        # creds (so a later token expiry re-announces) or the agent is forgotten.
        self._auth_announced: dict[int, set[str]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task[Any] | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Start the runtime if Agents mode is enabled and usable."""
        async with self._lock:
            if self._ready:
                return
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
                    env=dict(os.environ),
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
        await self._mark_interrupted_on_boot()
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        async with self._lock:
            if not self._ready:
                return
            self._ready = False
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
        """Flag sessions that were mid-turn when the process last died."""
        async with SessionLocal() as session:
            from sqlalchemy import update

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
        async with SessionLocal() as session:
            timeout = await resolve_agents_watchdog_timeout(session)
            cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
            rows = (
                (
                    await session.execute(
                        select(AgentSession).where(AgentSession.status == "running")
                    )
                )
                .scalars()
                .all()
            )
            stale: list[tuple[int, int | None, int | None]] = []
            reason = (
                f"No runtime activity for over {max(1, timeout // 60)} min — "
                "interrupted by the watchdog. Resume to retry."
            )
            for agent in rows:
                ref = agent.last_activity_at or agent.updated_at or agent.created_at
                if ref is None:
                    continue
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=UTC)
                if ref < cutoff:
                    agent.status = "interrupted"
                    agent.error = reason
                    stale.append((agent.id, agent.topic_id, agent.chat_id))
            if stale:
                await session.commit()
        # Drop any wedged live session so a Resume rebuilds it clean, then signal
        # the UI. Done outside the DB transaction to keep the commit tight.
        for agent_id, topic_id, chat_id in stale:
            logger.warning("agent %s: interrupted by watchdog (idle > %ss)", agent_id, timeout)
            with contextlib.suppress(Exception):
                await self.teardown_session(agent_id)
            await publish_agent_changed(
                agent_session_id=agent_id, topic_id=topic_id, chat_id=chat_id
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

    def _precursor_mcp_config(self) -> dict[str, Any] | None:
        """Translate the built-in 'precursor' MCP entry into an SDK stdio config.

        Attaching it lets the agent read topic context and post results back via
        the existing ``post_message`` tool (subject to the user's mcp_expose
        toggles). Returns ``None`` if the SDK isn't loadable.
        """
        try:
            sdk = runtime.load_sdk()
        except RuntimeError:
            return None
        # Reuse the same launcher the in-app MCP client uses, so there's one
        # definition of how to run the precursor server.
        env = dict(os.environ)
        # First-party access: agents bypass the external mcp_expose toggles so
        # they can read topic content and post results back.
        env["PRECURSOR_MCP_FULL_ACCESS"] = "1"
        config: Any = sdk.MCPStdioServerConfig(
            type="stdio",
            command=sys.executable,
            args=["-m", "precursor.backend.services.mcp.precursor_server"],
            env=env,
            # Expose all precursor tools — without this the runtime includes none
            # ([] is the default), so the agent can't read/post topic content.
            tools=["*"],
        )
        return {"precursor": config}

    @staticmethod
    def _entry_to_sdk_config(sdk: Any, entry: Any, github_token: str) -> Any:
        """Translate one ``MCPServerEntry`` into an SDK MCP server config.

        Raises ``ValueError`` for entries the SDK can't represent (missing
        command/url, unknown transport) so the caller can skip + log them.
        """
        if entry.transport == "stdio":
            if not entry.command:
                raise ValueError("stdio server has no command")
            return sdk.MCPStdioServerConfig(
                type="stdio",
                command=entry.command,
                args=list(entry.args),
                # Built-ins set their own env (or None → inherit ours so PATH and
                # the venv resolve); user entries always inherit ours.
                env=entry.env if entry.env is not None else dict(os.environ),
                tools=["*"],
            )
        if entry.transport == "streamable_http":
            if not entry.url:
                raise ValueError("streamable_http server has no url")
            # headers_provider folds in per-request secrets — the GitHub bearer
            # token for the built-in 'github' server, or a user entry's stored
            # headers. Resolved here, never persisted in agent events.
            headers = entry.headers_provider(github_token) if entry.headers_provider else None
            return sdk.MCPHTTPServerConfig(
                type="http",
                url=entry.url,
                headers=headers or None,
                tools=["*"],
            )
        raise ValueError(f"unsupported transport {entry.transport!r}")

    async def _enabled_catalog_fingerprint(self) -> frozenset[str]:
        """Names of catalog MCP servers currently enabled *and* registered.

        Excludes ``precursor`` (always attached with full access). Computed the
        same way on both sides of the comparison in :meth:`_ensure_live`, so it
        deliberately reflects the user's toggles rather than which servers
        actually attached — an OAuth server skipped for missing credentials must
        not read as a change and trigger an endless rebuild loop.
        """
        from precursor.backend.services.app_settings import resolve_mcp_enabled
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        async with SessionLocal() as session:
            enabled = await resolve_mcp_enabled(session)
        registered = {entry.name for entry in get_mcp_client_manager().list_entries()}
        return frozenset(
            name
            for name, on in enabled.items()
            if on and name != "precursor" and name in registered
        )

    async def _catalog_mcp_configs(
        self,
    ) -> tuple[dict[str, Any], datetime | None, list[str]]:
        """SDK configs for every catalog MCP server the user has *enabled*.

        Mirrors the chat/topics surface: both built-in servers (``github``,
        ``fetch``, ``workspace-fs``, …) and user-defined ones are attached when
        their ``mcp_enabled`` toggle is on, so an agent can call the same tools.
        ``precursor`` is excluded here — it's attached separately with full
        access in :meth:`_precursor_mcp_config`.

        Returns ``(configs, oauth_expires_at, auth_required)``: ``oauth_expires_at``
        is the soonest expiry across any OAuth-protected server whose bearer token
        we baked into a static header (so the caller can refresh before it lapses,
        ``None`` when nothing attached needs it); ``auth_required`` lists enabled
        OAuth servers we *skipped* because no valid credentials are available, so
        the caller can surface an interactive sign-in prompt instead of leaving
        the agent to discover the tools are silently missing. Returns
        ``({}, None, [])`` if the SDK isn't loadable.
        """
        try:
            sdk = runtime.load_sdk()
        except RuntimeError:
            return {}, None, []

        # Imported lazily to keep this module importable without the MCP service
        # graph in the import path of the agents-unavailable case.
        from precursor.backend.services.app_settings import resolve_mcp_enabled
        from precursor.backend.services.github_auth import resolve_github_token
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        async with SessionLocal() as session:
            enabled = await resolve_mcp_enabled(session)
            github_token = await resolve_github_token(session)

        manager = get_mcp_client_manager()
        configs: dict[str, Any] = {}
        oauth_expires_at: datetime | None = None
        auth_required: list[str] = []
        for entry in manager.list_entries():
            # 'precursor' is first-party and attached with full access elsewhere;
            # never gate or duplicate it here.
            if entry.name == "precursor":
                continue
            if not enabled.get(entry.name, False):
                continue
            try:
                config = self._entry_to_sdk_config(sdk, entry, github_token)
            except ValueError as exc:
                logger.warning("Skipping MCP server '%s': %s", entry.name, exc)
                continue
            # OAuth-protected catalog servers (WorkIQ preview) authenticate via an
            # httpx.Auth provider that the SDK's static-header HTTP config can't
            # carry. Mint a concrete bearer token and inject it, or skip the
            # server entirely when sign-in is required — attaching it without
            # credentials would just surface 401s as missing tools to the agent.
            if entry.transport == "streamable_http" and entry.auth_provider is not None:
                bearer = await self._oauth_bearer_header(entry.name)
                if bearer is None:
                    logger.warning(
                        "Skipping MCP server '%s' for agent: no valid credentials "
                        "(surfacing an in-app sign-in prompt)",
                        entry.name,
                    )
                    auth_required.append(entry.name)
                    continue
                header, expires_at = bearer
                # Unknown lifetime → assume a conservative TTL so we still rebuild
                # the session periodically rather than letting a stale header rot.
                if expires_at is None:
                    expires_at = datetime.now(UTC) + _OAUTH_FALLBACK_TTL
                oauth_expires_at = (
                    expires_at if oauth_expires_at is None else min(oauth_expires_at, expires_at)
                )
                existing = dict(config.get("headers") or {})
                existing.update(header)
                config["headers"] = existing
            configs[entry.name] = config
        return configs, oauth_expires_at, auth_required

    @staticmethod
    async def _oauth_bearer_header(name: str) -> tuple[dict[str, str], datetime | None] | None:
        """Resolve a static ``Authorization`` header for an OAuth catalog server.

        Only WorkIQ preview uses an ``auth_provider`` today; return ``None`` for
        anything else (or when no valid token is available) so the caller skips
        attaching it rather than handing the agent an unauthenticated endpoint.
        On success returns ``(header, expires_at)`` where ``expires_at`` may be
        ``None`` if the token's lifetime can't be determined.
        """
        if name != "workiq":
            return None
        from precursor.backend.services.mcp.workiq_preview import resolve_workiq_bearer_token

        resolved = await resolve_workiq_bearer_token()
        if resolved is None:
            return None
        token, expires_at = resolved
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}, expires_at

    async def _topic_context(self, agent: AgentSession) -> str | None:
        """Build a system-message preamble binding the agent to its topic.

        Without this the agent has no idea which topic it's attached to, so a
        request like "summarise the topic description" gets answered from the
        tool's field schema instead of the actual record. We give it the id,
        title and description, and point it at the precursor MCP tools to pull
        the rest on demand (and post results back).
        """
        if not agent.topic_id:
            return None
        async with SessionLocal() as session:
            topic = await session.get(Topic, agent.topic_id)
        if topic is None:
            return None
        lines = [
            "## Bound Precursor topic",
            "",
            f'You are operating on Precursor topic #{topic.id} ("{topic.title}").',
        ]
        description = (topic.description or "").strip()
        if description:
            lines += ["", "Topic description:", description]
        lines += [
            "",
            "Use the `precursor` MCP tools to work with it: `get_topic("
            f"{topic.id})` for metadata, `list_messages({topic.id})` to read the "
            "conversation, `search(...)` to find related content, and "
            f"`post_message({topic.id}, ...)` to write your results back to the "
            "topic. Prefer reading the live topic over assumptions.",
        ]
        return "\n".join(lines)

    async def _system_preamble(self, agent: AgentSession) -> str | None:
        """Combined system-message append: operator custom prompt + memory + topic binding.

        The SDK base prompt isn't ours to set, so each piece is *appended*. The
        custom prompt (Settings → Agents) comes first as general guidance,
        long-term memory follows as standing context (matching chat/topic turns),
        then the topic binding so the agent always knows which record it's on.
        """
        async with SessionLocal() as session:
            custom = (await resolve_agents_system_prompt(session)).strip()
            memory = await build_memory_prompt(session)
        topic = await self._topic_context(agent)
        parts = [p for p in (custom, memory, topic, SUGGESTIONS_INSTRUCTION) if p]
        return "\n\n".join(parts) if parts else None

    # ------------------------------------------------------------------ sessions

    async def _ensure_live(self, agent: AgentSession) -> _LiveSession:
        """Return the live SDK session for ``agent``, creating/resuming it.

        A cached session is reused unless its baked-in OAuth bearer header is
        about to expire (see :meth:`_oauth_stale`): the SDK can't refresh a static
        header, so we transparently tear the session down and recreate it, which
        re-mints the token while resuming the same conversation via
        ``copilot_session_id``. We never refresh mid-turn — only when the agent is
        idle, so an in-flight run is left untouched until its next dispatch.
        """
        self._require_ready()
        live = self._live.get(agent.id)
        if live is not None:
            oauth_stale = self._oauth_stale(live)
            catalog_changed = (
                live.mcp_fingerprint is not None
                and live.mcp_fingerprint != await self._enabled_catalog_fingerprint()
            )
            if not oauth_stale and not catalog_changed:
                return live
            if agent.status in {"running", "needs_approval", "pending"}:
                # A turn is in flight — don't disrupt it; refresh on the next
                # idle dispatch instead.
                return live
            reason = (
                "refresh an expiring OAuth token"
                if oauth_stale
                else "pick up a changed MCP server set"
            )
            logger.info("Rebuilding agent %s session to %s", agent.id, reason)
            await self.teardown_session(agent.id, forget=False)

        assert self._client is not None
        kwargs: dict[str, Any] = {
            "model": agent.model or get_settings().agents_default_model,
            "on_permission_request": self._make_permission_handler(agent.id),
        }
        # Reasoning effort + context tier are global agent prefs (Settings →
        # Agents / composer toolbar). Applied at session creation, mirroring how
        # the model is chosen — a change takes effect on the next new/rebuilt
        # session. The frontend only offers efforts the chosen model supports.
        async with SessionLocal() as s:
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
        if effort:
            kwargs["reasoning_effort"] = effort
        if tier and tier != "default":
            kwargs["context_tier"] = tier
        if agent.copilot_session_id:
            kwargs["session_id"] = agent.copilot_session_id
        mcp = self._precursor_mcp_config()
        oauth_expires_at: datetime | None = None
        auth_required: list[str] = []
        mcp_fingerprint: frozenset[str] | None = None
        if mcp is not None:
            # Attach every enabled catalog server (built-in + user-defined).
            # _catalog_mcp_configs already excludes 'precursor', so the
            # first-party full-access entry can't be shadowed.
            catalog, oauth_expires_at, auth_required = await self._catalog_mcp_configs()
            mcp.update(catalog)
            kwargs["mcp_servers"] = mcp
            # Snapshot the enabled set so a later toggle rebuilds this session.
            mcp_fingerprint = await self._enabled_catalog_fingerprint()
        preamble = await self._system_preamble(agent)
        if preamble:
            # Append (don't replace) so the agent keeps its SDK base instructions
            # but also gets the operator's custom guidance and any topic binding.
            kwargs["system_message"] = {"mode": "append", "content": preamble}

        sdk_session = await self._client.create_session(**kwargs)
        live = _LiveSession(
            sdk_session=sdk_session,
            oauth_expires_at=oauth_expires_at,
            mcp_fingerprint=mcp_fingerprint,
        )
        self._live[agent.id] = live

        # Wire the event stream. The SDK invokes this synchronously; defer the
        # async work (DB + bus) onto the loop.
        sdk_session.on(lambda event: self._spawn(self._handle_event(agent.id, event)))

        # Persist the resume handle the first time round.
        sid = getattr(sdk_session, "id", None) or getattr(sdk_session, "session_id", None)
        if sid and not agent.copilot_session_id:
            await self._patch(agent.id, copilot_session_id=str(sid))

        # Any OAuth server we couldn't attach for lack of credentials is surfaced
        # as an in-app sign-in prompt (drives the global McpAuthBanner) instead of
        # leaving the agent to hit "tool not available" and improvise an answer.
        await self._announce_auth_required(agent.id, auth_required)
        return live

    @staticmethod
    def _oauth_stale(live: _LiveSession) -> bool:
        """True when ``live``'s baked-in OAuth token is at/within the refresh margin."""
        expires_at = live.oauth_expires_at
        if expires_at is None:
            return False
        return datetime.now(UTC) >= expires_at - _OAUTH_REFRESH_MARGIN

    async def _auth_server_from_failed_tool(self, event: AgentEvent) -> str | None:
        """Return the OAuth server to prompt for when a tool failure looks like
        an expired sign-in, else ``None``.

        Only WorkIQ uses OAuth today. We require the event to name ``workiq`` as
        its server and the bearer to be genuinely unavailable, so a routine tool
        error (bad args, server-side fault) never nags the user to re-auth.
        """
        if event.tool_status != "error":
            return None
        server = (event.data or {}).get("server_name")
        if server != "workiq":
            return None
        from precursor.backend.services.mcp.workiq_preview import resolve_workiq_bearer_token

        if await resolve_workiq_bearer_token() is not None:
            return None
        return "workiq"

    async def _emit_synthetic(self, agent_id: int, event: AgentEvent) -> None:
        """Append a manager-originated event to the timeline (archive + publish).

        Used for events the SDK never sends — currently ``mcp_auth_required`` —
        so they persist in the durable timeline and reach the frontend over the
        same ``agent.changed`` bus as real SDK events.
        """
        await self._ensure_loaded(agent_id)
        event.at = datetime.now(UTC)
        self._events.setdefault(agent_id, []).append(event)
        await self._archive_event(agent_id, event)
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )

    async def _announce_auth_required(self, agent_id: int, servers: list[str]) -> None:
        """Surface a sign-in prompt for each ``server`` we couldn't authenticate.

        De-duped per agent so a held session doesn't re-announce on every rebuild.
        Servers that are *not* currently blocked are dropped from the announced
        set, so a later token expiry (or a sign-in that's since lapsed) prompts
        again rather than staying silent.
        """
        announced = self._auth_announced.setdefault(agent_id, set())
        for server in servers:
            if server in announced:
                continue
            announced.add(server)
            label = "WorkIQ" if server == "workiq" else server
            await self._emit_synthetic(
                agent_id,
                AgentEvent(
                    kind="mcp_auth_required",
                    tool_name=server,
                    text=f"{label} needs you to sign in to use its tools.",
                    data={"server": server},
                ),
            )
        # Reset servers that authenticated this build so a future lapse re-fires.
        announced.intersection_update(servers)

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
        for agent_id in list(self._live):
            agent = await self._load(agent_id)
            if agent is not None and agent.status in {"running", "needs_approval", "pending"}:
                continue
            await self.teardown_session(agent_id, forget=False)
            self._auth_announced.pop(agent_id, None)

    async def start_task(self, agent_id: int) -> None:
        agent = await self._load(agent_id)
        if agent is None:
            return
        live = await self._ensure_live(agent)
        live.approval_policy = await self._approval_policy()
        prompt = (agent.task_prompt or "").strip() or None
        live.pending_prompt = prompt
        live.pending_answer = None
        await self._patch(agent_id, status="running", error=None, active_prompt=prompt)
        await publish_agent_changed(
            agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
        )
        await live.sdk_session.send(agent.task_prompt)

    async def restart_with_task(self, agent_id: int) -> None:
        """Re-establish the SDK session after the task prompt was edited.

        The task is delivered only by :meth:`start_task`; a live or resumed
        session keeps the *previous* instructions in its history, so an edited
        ``task_prompt`` stays inert until it is replayed. Drop the in-memory
        session (``forget=False`` keeps the visible timeline) so the next connect
        refreshes the system preamble, then replay the new task.

        ``copilot_session_id`` is deliberately left untouched: scheduled
        ``/agent <uuid>`` nudges target that id, and recreating an agent (which
        mints a new id) is exactly what silently breaks such schedules. Callers
        that want a clean-slate context use the ``clear`` command instead.
        """
        await self.teardown_session(agent_id)
        await self.start_task(agent_id)

    async def send_message(self, agent_id: int, text: str) -> None:
        agent = await self._load(agent_id)
        if agent is None:
            return
        live = await self._ensure_live(agent)
        live.approval_policy = await self._approval_policy()
        prompt = text.strip() or None
        live.pending_prompt = prompt
        live.pending_answer = None
        await self._patch(agent_id, status="running", active_prompt=prompt)
        await publish_agent_changed(
            agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
        )
        await live.sdk_session.send(text)

    async def resume(self, agent_id: int) -> None:
        """Re-run the in-flight turn of an interrupted session.

        Re-sends the persisted ``active_prompt`` (the turn cut off by a restart
        or the watchdog) so it finishes and notifies back. A no-op when there's
        nothing tracked to resume.
        """
        agent = await self._load(agent_id)
        if agent is None:
            return
        prompt = (agent.active_prompt or "").strip()
        if not prompt:
            return
        live = await self._ensure_live(agent)
        live.approval_policy = await self._approval_policy()
        live.pending_prompt = prompt
        live.pending_answer = None
        await self._patch(agent_id, status="running", error=None)
        await publish_agent_changed(
            agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
        )
        await live.sdk_session.send(prompt)

    async def cancel(self, agent_id: int) -> None:
        live = self._live.get(agent_id)
        if live is not None:
            with contextlib.suppress(Exception):
                await live.sdk_session.abort()
            for fut in live.pending.values():
                if not fut.done():
                    fut.set_result(self._reject("cancelled"))
        await self._patch(agent_id, status="cancelled")
        await publish_agent_changed(agent_session_id=agent_id)

    async def resolve_permission(self, agent_id: int, request_id: str, decision: str) -> bool:
        """Resolve a parked permission request. Returns True if one matched."""
        live = self._live.get(agent_id)
        if live is None:
            return False
        fut = live.pending.get(request_id)
        if fut is None or fut.done():
            return False
        if decision == "approve-always":
            # Remember the action for the rest of the session (enforced locally by
            # the permission handler) and record the grant for the Settings recap.
            info = live.pending_info.get(request_id, {})
            live.session_approvals.add(self._signature(info))
            live.grants.append(
                {
                    "type": info.get("type", "tool"),
                    "title": info.get("title"),
                    "target": info.get("command")
                    or info.get("path")
                    or info.get("url")
                    or info.get("tool")
                    or info.get("server"),
                    "at": datetime.now(UTC),
                }
            )
        fut.set_result(self._decision(decision))
        return True

    def list_permissions(self) -> list[dict[str, Any]]:
        """Recap of active "approve for session" grants across live sessions."""
        out: list[dict[str, Any]] = []
        for agent_id, live in self._live.items():
            for grant in live.grants:
                out.append({"agent_id": agent_id, **grant})
        out.sort(key=lambda g: g.get("at") or datetime.min.replace(tzinfo=UTC), reverse=True)
        return out

    async def reset_permissions(self) -> int:
        """Revoke all session grants by disconnecting every live session.

        Tearing the SDK sessions down drops their in-session approvals; they're
        recreated fresh (and will ask again) on next use. Returns the count of
        grants cleared.
        """
        cleared = sum(len(live.grants) for live in self._live.values())
        agent_ids = list(self._live.keys())
        for agent_id in agent_ids:
            await self.teardown_session(agent_id)
        return cleared

    async def get_events(self, agent_id: int) -> list[AgentEvent]:
        """Return the normalised event history for the workflow timeline."""
        await self._ensure_loaded(agent_id)
        live = self._live.get(agent_id)
        events = list(self._events.get(agent_id, []))
        if not events:
            # Nothing archived (neither in memory nor the DB) — e.g. a session
            # resumed after a restart that hasn't re-emitted yet. Fall back to
            # whatever the live session can replay.
            if live is None:
                agent = await self._load(agent_id)
                if agent is None or not agent.copilot_session_id:
                    return []
                live = await self._ensure_live(agent)
            try:
                raw = await live.sdk_session.get_events()
            except Exception:
                logger.debug("get_events failed for agent %s", agent_id, exc_info=True)
                raw = []
            events = [self._normalise(ev) for ev in raw or []]
        # Append any unresolved permission requests as inline workflow steps so
        # the approval card renders in-place (with details of what's requested)
        # rather than floating detached from the timeline.
        if live is not None:
            for info in live.pending_info.values():
                events.append(
                    AgentEvent(
                        kind="permission_request",
                        text=info.get("title"),
                        request_id=info.get("request_id"),
                        data=info,
                    )
                )
        return events

    async def teardown_session(self, agent_id: int, *, forget: bool = False) -> None:
        """Disconnect a live session (e.g. before deleting the row).

        The archived timeline is kept by default so linking a topic — which
        recreates the session to re-inject context — doesn't wipe the workflow
        view. Pass ``forget=True`` when the agent is being deleted.
        """
        live = self._live.pop(agent_id, None)
        if live is not None:
            with contextlib.suppress(Exception):
                await live.sdk_session.disconnect()
        if forget:
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

    # ------------------------------------------------------------------ commands

    async def clear_session(self, agent_id: int, *, keep_id: bool = False) -> None:
        """Erase an agent's conversation and start its SDK context from scratch.

        Disconnects + forgets the live session and wipes the archived timeline
        (``teardown_session(forget=True)``), then resets the in-flight/status
        fields back to idle so the next message opens a brand-new SDK session
        with no prior history resumed.

        ``keep_id`` selects what happens to the public handle:

        * ``False`` (default, interactive ``/clear``) mints a **fresh**
          ``copilot_session_id`` — a brand-new, shareable conversation.
        * ``True`` keeps the **same** ``copilot_session_id`` and instead deletes
          the SDK's on-disk state for it, so a scheduled ``/agent <uuid>``
          reference (which targets that id) keeps resolving while still getting a
          clean context on the next turn. Without the delete, reusing the id
          would resume the old transcript from disk and defeat the clear.
        """
        old_id: str | None = None
        if keep_id:
            agent = await self._load(agent_id)
            old_id = agent.copilot_session_id if agent else None

        await self.teardown_session(agent_id, forget=True)

        patch: dict[str, Any] = {
            "status": "idle",
            "active_prompt": None,
            "result_summary": None,
            "error": None,
        }
        if keep_id:
            # Best-effort: a never-connected (pending) agent has nothing on disk.
            if old_id and self._client is not None:
                with contextlib.suppress(Exception):
                    await self._client.delete_session(old_id)
        else:
            patch["copilot_session_id"] = str(uuid.uuid4())
        await self._patch(agent_id, **patch)
        await self._publish(agent_id)

    async def rerun_task(self, agent_id: int, *, extra: str | None = None) -> None:
        """Reset the agent's context (same uuid) and replay its stored task.

        Backs the scheduled ``/agent <uuid> /run`` nudge: instead of the schedule
        re-sending the full instruction block every run (and replaying an
        ever-growing transcript), the instructions live **once** in the agent's
        ``task_prompt``. Each run wipes the prior transcript via
        :meth:`clear_session` (``keep_id=True`` so the schedule's uuid keeps
        resolving), then re-delivers ``task_prompt`` — optionally with an
        ``extra`` one-off note appended for this run — as a clean turn.
        """
        await self.clear_session(agent_id, keep_id=True)
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

    async def _cmd_rename(self, agent_id: int, argument: str) -> None:
        title = " ".join(argument.split())[:200]
        if not title:
            raise ValueError("Usage: /rename <new title>")
        await self._patch(agent_id, title=title)
        await self._publish(agent_id)

    async def _cmd_archive(self, agent_id: int, argument: str) -> None:
        agent = await self._load(agent_id)
        if agent is not None and agent.archived_at is None:
            await self._patch(agent_id, archived_at=datetime.now(UTC))
            await self._publish(agent_id)

    async def _cmd_clear(self, agent_id: int, argument: str) -> None:
        await self.clear_session(agent_id)

    async def _cmd_memory_store(self, agent_id: int, argument: str) -> None:
        from precursor.backend.services import memories as memory_service

        payload = memory_service.parse_store_arg(argument)
        async with SessionLocal() as session:
            await memory_service.create_memory(session, payload)

    async def _cmd_memory_update(self, agent_id: int, argument: str) -> None:
        from precursor.backend.services import memories as memory_service

        memory_id, payload = memory_service.parse_update_arg(argument)
        async with SessionLocal() as session:
            try:
                await memory_service.update_memory(session, memory_id, payload)
            except LookupError as exc:
                raise ValueError(str(exc)) from exc

    # Registry of system slash commands available inside an agent session:
    # name -> async handler. The set of supported names (used for validation and
    # the rejection message) is derived from these keys, and the frontend picker
    # mirrors it via AGENT_SLASH_COMMANDS, so a new command is a single entry.
    _COMMAND_HANDLERS: ClassVar[dict[str, Callable[[AgentManager, int, str], Awaitable[None]]]] = {
        "rename": _cmd_rename,
        "archive": _cmd_archive,
        "clear": _cmd_clear,
        "memory-store": _cmd_memory_store,
        "memory-update": _cmd_memory_update,
    }

    @classmethod
    def supported_commands(cls) -> tuple[str, ...]:
        """Names of slash commands an agent session accepts (registry keys)."""
        return tuple(cls._COMMAND_HANDLERS)

    async def _publish(self, agent_id: int) -> None:
        """Emit an ``agent.changed`` signal for an agent by id (loads its links)."""
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )

    async def list_models(self) -> list[dict[str, Any]]:
        """Return the runtime's available models, or empty.

        Used to populate the model picker. Surfaces each model's context window
        and advertised reasoning-effort set so the composer can adapt its
        controls. The SDK caches the result after the first call.
        """
        if not self._ready or self._client is None:
            return []
        try:
            models = await self._client.list_models()
        except Exception:
            logger.debug("list_models failed", exc_info=True)
            return []
        out: list[dict[str, Any]] = []
        for m in models or []:
            mid = getattr(m, "id", None)
            if not mid:
                continue
            caps = getattr(m, "capabilities", None)
            limits = getattr(caps, "limits", None) if caps is not None else None
            ctx = None
            if limits is not None:
                ctx = getattr(limits, "max_prompt_tokens", None) or getattr(
                    limits, "max_context_window_tokens", None
                )
            efforts = getattr(m, "supported_reasoning_efforts", None) or []
            out.append(
                {
                    "id": str(mid),
                    "name": str(getattr(m, "name", None) or mid),
                    "context_window": int(ctx) if isinstance(ctx, (int, float)) else None,
                    "supported_reasoning_efforts": [str(e) for e in efforts],
                }
            )
        return out

    async def apply_session_overrides(self) -> None:
        """Apply the current global model / reasoning-effort / context-tier prefs
        onto idle live sessions.

        Lets a change in the composer (or Settings → Agents) take effect on the
        next message of an in-progress conversation instead of only new sessions.
        Uses the SDK's ``set_model`` — history-preserving, effective next turn.
        Skips sessions with a turn in flight, where switching the model is unsafe;
        those pick the change up on their next idle dispatch.
        """
        if not self._ready:
            return
        live_ids = list(self._live.keys())
        if not live_ids:
            return
        async with SessionLocal() as s:
            default_model = await resolve_agents_default_model(s)
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
            rows = (
                (await s.execute(select(AgentSession).where(AgentSession.id.in_(live_ids))))
                .scalars()
                .all()
            )
        by_id = {a.id: a for a in rows}
        for agent_id in live_ids:
            live = self._live.get(agent_id)
            agent = by_id.get(agent_id)
            if live is None or agent is None:
                continue
            if agent.status in {"running", "needs_approval", "pending"}:
                continue
            model = agent.model or default_model
            if not model:
                continue
            # Always send the tier (incl. "default") so toggling back resets it;
            # a falsy effort is sent as None so the runtime restores the model
            # default rather than pinning a stale level.
            kwargs: dict[str, Any] = {"context_tier": tier or "default"}
            if effort:
                kwargs["reasoning_effort"] = effort
            try:
                await live.sdk_session.set_model(model, **kwargs)
            except Exception:
                logger.debug("set_model failed for agent %s", agent_id, exc_info=True)

    def _make_permission_handler(self, agent_id: int) -> Any:
        async def handler(request: Any, invocation: Any) -> Any:
            # The default approval policy decides how much we gate. ``autonomous``
            # approves everything; ``balanced`` (default) auto-approves read-only
            # intents (reads, URL fetches, read-only MCP) and our own precursor
            # MCP calls; ``manual`` asks for everything. Anything not auto-approved
            # is parked for explicit user approval.
            #
            # Read the policy cached on the live session (resolved once per turn);
            # never touch the DB here. If anything in the body raises, fall back to
            # the in-memory settings policy instead of letting the exception become
            # a silent, detail-less SDK denial.
            req_name = type(request).__name__
            try:
                live = self._live.get(agent_id)
                policy = (
                    live.approval_policy if live else None
                ) or get_settings().agents_approval_policy
                logger.info(
                    "agent %s: permission handler hit — request=%s policy=%s live=%s",
                    agent_id,
                    req_name,
                    policy,
                    live is not None,
                )
                if policy == "autonomous":
                    logger.info("agent %s: %s auto-approved (autonomous)", agent_id, req_name)
                    return self._approve_once()
                if policy != "manual" and self._should_auto_approve(request):
                    logger.info("agent %s: %s auto-approved (read-only)", agent_id, req_name)
                    return self._approve_once()
                info = self._describe_permission(request)
                # Honour a prior "approve for session" for the same action.
                if live is not None and self._signature(info) in live.session_approvals:
                    logger.info("agent %s: %s auto-approved (session grant)", agent_id, req_name)
                    return self._approve_once()
                logger.info(
                    "agent %s: %s requires approval — parking (%s)",
                    agent_id,
                    req_name,
                    info.get("title"),
                )
                return await self._park_permission(agent_id, request, info)
            except asyncio.CancelledError:
                raise
            except Exception:
                fallback = get_settings().agents_approval_policy
                logger.exception(
                    "agent %s: permission handler failed for %s; falling back to %r policy",
                    agent_id,
                    req_name,
                    fallback,
                )
                # Don't silently deny in unattended modes — that's the bug we're
                # guarding against. Manual mode can't safely auto-approve, so emit
                # an explicit rejection the UI can show rather than a crash.
                if fallback != "manual":
                    return self._approve_once()
                return self._reject("permission handler error")

        return handler

    async def _approval_policy(self) -> str:
        try:
            async with SessionLocal() as session:
                return await resolve_agents_approval_policy(session)
        except Exception:
            fallback = get_settings().agents_approval_policy
            logger.warning(
                "agent: approval-policy DB read failed; using in-memory default %r",
                fallback,
                exc_info=True,
            )
            return fallback

    @staticmethod
    def _signature(info: dict[str, Any]) -> tuple[str, str | None]:
        """A stable key for "approve for session": the action and its target."""
        target = (
            info.get("command")
            or info.get("path")
            or info.get("url")
            or info.get("tool")
            or info.get("server")
        )
        return (str(info.get("type", "tool")), str(target) if target else None)

    @staticmethod
    def _should_auto_approve(request: Any) -> bool:
        name = type(request).__name__
        if name in ("PermissionRequestRead", "PermissionRequestUrl"):
            return True
        if name == "PermissionRequestMcp":
            server = str(getattr(request, "server_name", "") or "")
            return server == "precursor" or bool(getattr(request, "read_only", False))
        return False

    @staticmethod
    def _describe_permission(request: Any) -> dict[str, Any]:
        """Normalise a permission request into a UI-friendly description."""

        def g(attr: str) -> Any:
            value = getattr(request, attr, None)
            return value if value not in ("",) else None

        name = type(request).__name__.replace("PermissionRequest", "") or "Tool"
        info: dict[str, Any] = {"type": name.lower(), "title": f"{name} permission"}
        if name == "Shell":
            info.update(
                title="Run a shell command",
                command=g("full_command_text"),
                intention=g("intention"),
                warning=g("warning"),
            )
        elif name == "Write":
            info.update(
                title="Write to a file",
                path=g("file_name"),
                intention=g("intention"),
                diff=(str(g("diff"))[:4000] if g("diff") else None),
            )
        elif name == "Read":
            info.update(title="Read a file", path=g("path"), intention=g("intention"))
        elif name == "Mcp":
            tool = g("tool_title") or g("tool_name")
            info.update(
                title=f"Use MCP tool: {tool}" if tool else "Use an MCP tool",
                server=g("server_name"),
                tool=g("tool_name"),
            )
        elif name == "Url":
            info.update(title="Fetch a URL", url=g("url"), intention=g("intention"))
        elif name == "Memory":
            info.update(title="Update memory", fact=g("fact"), reason=g("reason"))
        elif name == "CustomTool":
            tool = g("tool_name")
            info.update(
                title=f"Use tool: {tool}" if tool else "Use a tool",
                tool=tool,
                detail=g("tool_description"),
            )
        return {k: v for k, v in info.items() if v is not None}

    async def _park_permission(
        self, agent_id: int, request: Any, info: dict[str, Any] | None = None
    ) -> Any:
        live = self._live.get(agent_id)
        if live is None:
            logger.warning(
                "agent %s: cannot park permission — no live session; rejecting", agent_id
            )
            return self._reject("session gone")
        request_id = str(getattr(request, "tool_call_id", "") or id(request))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        live.pending[request_id] = fut
        live.pending_info[request_id] = {
            "request_id": request_id,
            **(info if info is not None else self._describe_permission(request)),
        }
        await self._patch(agent_id, status="needs_approval")
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )
        try:
            return await fut
        finally:
            live.pending.pop(request_id, None)
            live.pending_info.pop(request_id, None)

    def _approve_once(self) -> Any:
        return runtime.load_rpc().PermissionDecisionApproveOnce()

    def _reject(self, feedback: str) -> Any:
        return runtime.load_rpc().PermissionDecisionReject(feedback=feedback)

    def _decision(self, decision: str) -> Any:
        rpc = runtime.load_rpc()
        if decision == "deny":
            return rpc.PermissionDecisionReject(feedback="Denied by user")
        # Both approve-once and approve-for-session approve the *current* request
        # with the same SDK call. We don't emit PermissionDecisionApproveForSession
        # — its mandatory ``approval`` object for command/write prompts is what
        # triggers the runtime's "missing approval field" error. Session scope is
        # instead enforced by ``session_approvals`` in the permission handler.
        return rpc.PermissionDecisionApproveOnce()

    # ------------------------------------------------------------------ events

    async def _ensure_loaded(self, agent_id: int) -> None:
        """Hydrate the in-memory timeline from the ``agent_events`` archive once.

        After a process restart the live cache is empty and the SDK only replays
        ``SessionStartData`` on resume, so the durable history lives only in the
        DB. Load it lazily the first time an agent is touched (an event arriving
        or a timeline read) and mark it loaded so we don't re-read per event.
        """
        if agent_id in self._loaded:
            return
        async with self._events_lock:
            if agent_id in self._loaded:
                return
            async with SessionLocal() as session:
                payloads = (
                    await session.scalars(
                        select(AgentEventRecord.payload)
                        .where(AgentEventRecord.agent_session_id == agent_id)
                        .order_by(AgentEventRecord.id)
                    )
                ).all()
            archived: list[AgentEvent] = []
            for payload in payloads:
                try:
                    archived.append(AgentEvent.model_validate_json(payload))
                except Exception:
                    logger.debug(
                        "skipping malformed archived event for agent %s", agent_id, exc_info=True
                    )
            if archived:
                self._events[agent_id] = archived
            self._loaded.add(agent_id)

    async def _archive_event(self, agent_id: int, event: AgentEvent) -> None:
        """Persist one normalised event to the durable timeline archive."""
        try:
            async with SessionLocal() as session:
                session.add(
                    AgentEventRecord(
                        agent_session_id=agent_id,
                        payload=event.model_dump_json(),
                    )
                )
                await session.commit()
        except Exception:
            logger.debug("failed to archive event for agent %s", agent_id, exc_info=True)

    async def _handle_event(self, agent_id: int, event: Any) -> None:
        # Serialise per-agent so events are handled in arrival order: the idle
        # handler must run *after* the assistant-message handler has committed
        # ``result_summary``, otherwise ``_notify_back`` posts the previous
        # turn's answer.
        lock = self._event_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            await self._handle_event_locked(agent_id, event)

    async def _handle_event_locked(self, agent_id: int, event: Any) -> None:
        # Archive every event so the timeline persists across session teardown
        # (e.g. on topic link) and process restart, where the SDK would otherwise
        # drop it (``get_events`` only replays ``SessionStartData`` on resume).
        await self._ensure_loaded(agent_id)
        normalised = self._normalise(event)
        normalised.at = datetime.now(UTC)
        self._events.setdefault(agent_id, []).append(normalised)
        await self._archive_event(agent_id, normalised)

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

        if name == "AssistantMessageData":
            content = getattr(data, "content", None)
            if content:
                patch["result_summary"] = str(content)[:2000]
                # Keep the full answer for the topic/chat repost — the summary
                # column is capped for the agent list.
                live = self._live.get(agent_id)
                if live is not None:
                    live.pending_answer = str(content)
        elif name == "AssistantUsageData":
            await self._record_usage(agent_id, data)
        elif name in ("SessionIdleData", "SystemNotificationAgentIdle"):
            agent = await self._load(agent_id)
            # Don't let a trailing idle event mask a turn that just errored or
            # was paused/cancelled — those statuses are sticky so the failure
            # stays visible (and the in-flight prompt stays resumable).
            if agent is not None and agent.status not in (
                "needs_approval",
                "cancelled",
                "failed",
            ):
                patch["status"] = "idle"
                # The turn has finished — drop the durable in-flight prompt so a
                # later resume can't re-run an already-completed turn.
                patch["active_prompt"] = None
                await self._notify_back(agent)
        elif name in ("AbortData",):
            patch["status"] = "cancelled"
        elif name in ("ErrorData", "SessionErrorData"):
            patch["status"] = "failed"
            patch["error"] = str(getattr(data, "message", name))[:2000]

        await self._patch(agent_id, **patch)
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )

    async def _record_usage(self, agent_id: int, data: Any) -> None:
        """Meter an ``AssistantUsageData`` round into the shared usage ledger.

        Each agent LLM call lands as one ``source="agent"`` row tagged with the
        agent's linked container, so agent spend shows up in the global usage
        stats alongside chat/topic turns. ``SessionUsageInfoData`` is *not*
        recorded — it reports context-window occupancy, not billable deltas, so
        counting it would double-charge the turn.
        """
        prompt_tokens = int(getattr(data, "input_tokens", None) or 0)
        completion_tokens = int(getattr(data, "output_tokens", None) or 0)
        if not prompt_tokens and not completion_tokens:
            return
        model = getattr(data, "model", None)
        agent = await self._load(agent_id)
        if agent is None:
            return
        try:
            async with SessionLocal() as session:
                await record_usage(
                    session,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    source="agent",
                    model=str(model) if model else agent.model,
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                )
                await session.commit()
        except Exception:
            logger.debug("failed to record agent usage for %s", agent_id, exc_info=True)

    async def _notify_back(self, agent: AgentSession) -> None:
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

        live = self._live.get(agent.id)
        if live is None or live.pending_prompt is None:
            return
        prompt = live.pending_prompt
        live.pending_prompt = None

        # Prefer the full assistant text captured this turn; fall back to the
        # (capped) summary so a resumed turn without a tracked answer still posts.
        answer = (
            live.pending_answer or agent.result_summary or ""
        ).strip() or "Agent task finished."
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

    def _normalise(self, event: Any) -> AgentEvent:
        """Map a raw SDK event onto the workflow-timeline shape."""
        data = getattr(event, "data", event)
        name = type(data).__name__
        # ``message`` covers error events (SessionErrorData/ErrorData) whose detail
        # lives there rather than in ``content``/``text`` — otherwise their
        # timeline node renders blank.
        text = (
            getattr(data, "content", None)
            or getattr(data, "text", None)
            or getattr(data, "message", None)
        )
        tool_name = getattr(data, "tool_name", None) or getattr(data, "name", None)
        kind = _EVENT_KINDS.get(name, name)
        tool_status: str | None = None
        if "ToolRequest" in name or kind == "tool_call":
            tool_status = "running"
        elif "ToolResult" in name or kind == "tool_result":
            tool_status = "error" if getattr(data, "is_error", False) else "done"
        # Capture tool I/O (and error diagnostics) so the UI can show "what was
        # done" / "why it failed" on demand.
        extra: dict[str, Any] = {}
        for attr in (
            "arguments",
            "input",
            "result",
            "output",
            "server_name",
            "error_type",
            "error_code",
            "status_code",
        ):
            val = getattr(data, attr, None)
            if val is None:
                continue
            if attr in ("result", "output"):
                val = self._unwrap_result(val)
            if isinstance(val, str):
                extra[attr] = val[:_TOOL_RESULT_CAP]
            else:
                extra[attr] = self._jsonify(val)
        # ``ToolExecutionCompleteData`` reports success + result/error as nested
        # objects rather than the flat ``is_error``/``result`` string attrs the
        # loop above looks for, so a *failed* tool would otherwise archive as
        # ``data: null`` with no status — losing the reason the agent hit a wall
        # (e.g. a sandbox "permission denied" or a fetch error). Pull them out
        # explicitly so the timeline shows why a tool call failed.
        if name == "ToolExecutionCompleteData":
            success = getattr(data, "success", None)
            if success is not None:
                tool_status = "done" if success else "error"
                extra["success"] = bool(success)
            sandboxed = getattr(data, "sandboxed", None)
            if sandboxed is not None:
                # Surfaces that a command ran in the ephemeral cmd-runner jail —
                # key context when file writes silently don't persist.
                extra["sandboxed"] = bool(sandboxed)
            err = getattr(data, "error", None)
            if err is not None:
                message = getattr(err, "message", None) or str(err)
                extra["error"] = str(message)[:_TOOL_RESULT_CAP]
                code = getattr(err, "code", None)
                if code:
                    extra["error_code"] = str(code)
            if "result" not in extra:
                content = self._unwrap_result(getattr(data, "result", None))
                if isinstance(content, str) and content.strip():
                    extra["result"] = content[:_TOOL_RESULT_CAP]
            if not tool_name:
                desc = getattr(data, "tool_description", None)
                tool_name = getattr(desc, "name", None) or getattr(desc, "tool_name", None)
        # Usage events carry token counts, not tool I/O. Capture them verbatim
        # (as raw ints, not JSON-stringified) so the workflow timeline can drive
        # the per-agent usage stats in the side panel: ``AssistantUsageData``
        # meters each LLM round, ``SessionUsageInfoData`` reports the live
        # context-window occupancy.
        if name == "AssistantUsageData":
            for attr in ("input_tokens", "output_tokens", "reasoning_tokens"):
                val = getattr(data, attr, None)
                if val is not None:
                    extra[attr] = int(val)
            # The resolved model for this LLM round (a required SDK field). Lets
            # the UI show the concrete model per turn — useful for default-model
            # agents whose session.model is null.
            model = getattr(data, "model", None)
            if model:
                extra["model"] = str(model)
        elif name == "SessionUsageInfoData":
            for attr in ("current_tokens", "token_limit", "conversation_tokens"):
                val = getattr(data, attr, None)
                if val is not None:
                    extra[attr] = int(val)
        return AgentEvent(
            kind=kind,
            text=str(text) if text is not None else None,
            tool_name=str(tool_name) if tool_name else None,
            tool_status=tool_status,
            request_id=getattr(data, "tool_call_id", None),
            data=extra or None,
        )

    @staticmethod
    def _unwrap_result(value: Any) -> Any:
        """Pull readable text out of SDK result wrappers.

        Tool results arrive as ``ToolExecutionCompleteResult`` objects whose
        repr would otherwise leak into the UI; surface their content instead.
        """
        if isinstance(value, str):
            return value
        for attr in ("content", "detailed_content"):
            inner = getattr(value, attr, None)
            if isinstance(inner, str) and inner.strip():
                return inner
        return value

    @staticmethod
    def _jsonify(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str, indent=2)
        except (TypeError, ValueError):
            return str(value)

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


# Map SDK event class names → coarse workflow step kinds for the UI.
_EVENT_KINDS: dict[str, str] = {
    "AssistantMessageData": "assistant_message",
    "AssistantMessageDeltaData": "assistant_delta",
    "AssistantReasoningData": "reasoning",
    "AssistantReasoningDeltaData": "reasoning_delta",
    "AssistantTurnStartData": "turn_start",
    "AssistantTurnEndData": "turn_end",
    "AssistantUsageData": "usage",
    "SessionUsageInfoData": "context_usage",
    "SessionIdleData": "idle",
    "AbortData": "aborted",
}

_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
