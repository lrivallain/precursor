"""Live SDK sessions: building or resuming one per run (model choice, MCP
servers, preamble, hooks), rebuilding stale ones, and tearing them down.

``AgentManager`` delegates here; this module must not import ``manager`` at
runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentEventRecord,
    AgentRun,
    AgentSession,
)
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents import (
    prompting,
    timeline,
)
from precursor.backend.services.agents.live_session import _LiveSession
from precursor.backend.services.agents.mcp_scope import (
    parse_mcp_scope,
    scope_includes_precursor,
)
from precursor.backend.services.app_settings import (
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_file_change_tracking,
    resolve_agents_reasoning_effort,
)
from precursor.backend.services.events import publish_agent_changed
from precursor.backend.services.model_fallbacks import is_model_rejection, resolve_model_category

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)


def _accepts_kwarg(func: Any, name: str) -> bool:
    """Whether ``func`` takes keyword ``name`` (an older SDK may not)."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class SessionPool:
    def __init__(self, manager: AgentManager) -> None:
        # A back-reference read at call time (never captured bound methods), so a
        # test patching the manager still takes effect.
        self._manager = manager

    async def ensure_live(self, agent: AgentSession, run: AgentRun) -> _LiveSession:
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
        m = self._manager
        await m._ensure_runtime()
        m._require_ready()
        lock = m._live_locks.setdefault(run.id, asyncio.Lock())
        async with lock:
            return await m._ensure_live_locked(agent, run)

    async def _reuse(self, agent: AgentSession, run: AgentRun) -> _LiveSession | None:
        """The run's cached session, unless it has gone stale and was torn down."""
        m = self._manager
        live = m._live.get(run.id)
        if live is None:
            return None
        live.last_used = time.monotonic()
        oauth_stale = m._oauth_stale(live)
        catalog_changed = (
            live.mcp_fingerprint is not None
            and live.mcp_fingerprint != await m._expected_mcp_fingerprint(run)
        )
        # A server we had to skip for missing credentials doesn't move the
        # fingerprint (which tracks *enabled* servers), so without this the
        # tool-less session would be reused forever. Comparing credential
        # stamps rather than names means a rebuild that still can't attach
        # re-records the same stamps and doesn't fire again.
        auth_recovered = live.mcp_auth_skipped != await m._auth_skipped_stamps(
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
        await m._teardown_run(run.id, forget=False)
        return None

    async def _attach_mcp(
        self, agent: AgentSession, run: AgentRun, kwargs: dict[str, Any]
    ) -> tuple[datetime | None, list[str]]:
        """Put the run's MCP servers into ``kwargs``; returns the OAuth expiry and
        the servers that could not be attached for lack of credentials."""
        m = self._manager
        # An agent with MCP switched off gets no tool servers at all — not even
        # the first-party one. Whole catalogues of tool schemas are a large,
        # fixed context cost, so a step that only has to transform text pays
        # nothing for tools it will never call. An explicitly *empty* server
        # scope says the same thing in the other vocabulary, and means it.
        scope = parse_mcp_scope(run.mcp_servers)
        scoped_to_nothing = scope is not None and not scope
        tools_on = run.use_mcp and not scoped_to_nothing
        if not tools_on:
            return None, []
        mcp: dict[str, Any] = {}
        # ``precursor`` ignores the mcp_enabled toggle (it's first-party and
        # always available) but is not exempt from the scope — see
        # scope_includes_precursor. Attached here rather than via
        # _catalog_mcp_configs so it keeps its full-access env.
        if scope_includes_precursor(scope):
            mcp.update(m._mcp.precursor_mcp_config(agent) or {})
        # Every enabled catalog server the scope allows (built-in +
        # user-defined). _catalog_mcp_configs already skips 'precursor', so
        # the first-party full-access entry above can't be shadowed.
        catalog, oauth_expires_at, auth_required = await m._catalog_mcp_configs(scope)
        mcp.update(catalog)
        if mcp:
            kwargs["mcp_servers"] = mcp
        return oauth_expires_at, auth_required

    async def ensure_live_locked(self, agent: AgentSession, run: AgentRun) -> _LiveSession:
        m = self._manager
        reused = await self._reuse(agent, run)
        if reused is not None:
            return reused

        assert m._client is not None
        kwargs: dict[str, Any] = {
            "on_permission_request": m._permissions.make_handler(run.id),
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
            track_files = await resolve_agents_file_change_tracking(s)
            category = await resolve_model_category(s, agents=True)
        category = category if not (run.model or agent.model) else None
        model = run.model or default_model
        requested_model = (model, effort or None, tier or "default")
        model_choices = await m._model_selection.choices(
            agent.id, model, effort, tier, category=category
        )
        if run.copilot_session_id:
            kwargs["session_id"] = run.copilot_session_id
        # Lets a rewind put back the files the dropped turns changed. Only a
        # session tracked from its first turn can, so a resumed untracked one
        # stays conversation-only (the SDK reports that).
        if track_files and _accepts_kwarg(m._client.create_session, "enable_file_change_tracking"):
            kwargs["enable_file_change_tracking"] = True
        oauth_expires_at, auth_required = await self._attach_mcp(agent, run, kwargs)
        # Snapshot what this session carries so a later toggle — or a rebuilt run
        # with a different scope — rebuilds it. Computed by the same method the
        # reuse check compares against, so the two can't drift.
        mcp_fingerprint = await m._expected_mcp_fingerprint(run)
        preamble = await prompting.system_preamble(agent, run)
        if preamble:
            # Append (don't replace) so the agent keeps its SDK base instructions
            # but also gets the operator's custom guidance and any topic binding.
            kwargs["system_message"] = {"mode": "append", "content": preamble}

        async def message_reference_context(_input: Any, _context: Any) -> dict[str, str] | None:
            await m._transcript.ensure_loaded(agent.id)
            references = prompting.message_references(agent, m._events.get(agent.id, []), run.id)
            return {"additionalContext": references} if references else None

        kwargs["hooks"] = {"on_user_prompt_submitted": message_reference_context}

        attempted_models: set[tuple[str, str | None, str]] = set()
        for choice in model_choices:
            model, selected_effort, tier = choice
            attempted_models.add(choice)
            kwargs.pop("reasoning_effort", None)
            kwargs.pop("context_tier", None)
            if model:
                kwargs["model"] = model
            if selected_effort:
                kwargs["reasoning_effort"] = selected_effort
            if tier != "default":
                kwargs["context_tier"] = tier
            try:
                sdk_session = await m._client.create_session(**kwargs)
                break
            except Exception as exc:
                if not is_model_rejection(exc) or choice == model_choices[-1]:
                    raise
                logger.warning("agent %s: model configuration rejected: %s", agent.id, exc)
        live = _LiveSession(
            sdk_session=sdk_session,
            oauth_expires_at=oauth_expires_at,
            mcp_fingerprint=mcp_fingerprint,
            mcp_auth_skipped=await m._auth_skipped_stamps(auth_required),
            model_signature=choice if model else None,
            requested_model_signature=requested_model,
            requested_model_category=category,
            model_candidates=model_choices,
            model_attempts=attempted_models,
        )
        m._live[run.id] = live
        # Synchronous index so ``live_activity`` (no ``await`` available) can find
        # the run driving an agent without a DB round-trip.
        m._agent_runs[agent.id] = run.id

        # Wire the event stream. The SDK invokes this synchronously, in emission
        # order; the async work (DB + bus) is queued to run in that same order.
        agent_id, run_id = agent.id, run.id
        sdk_session.on(lambda event: m._dispatch_sdk_event(agent_id, run_id, event))
        if choice != requested_model:
            await m._model_selection.announce(run.id, choice, category=category)

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
        await m._announce_auth_required(agent.id, auth_required)
        return live

    async def emit_synthetic(self, agent_id: int, event: AgentEvent) -> None:
        """Append a manager-originated event to the timeline (archive + publish).

        Used for events the SDK never sends — currently ``mcp_auth_required`` —
        so they persist in the durable timeline and reach the frontend over the
        same ``agent.changed`` bus as real SDK events.
        """
        m = self._manager
        await m._transcript.ensure_loaded(agent_id)
        event.at = datetime.now(UTC)
        m._events.setdefault(agent_id, []).append(event)
        await timeline.archive_event(agent_id, event)
        agent = await m._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )

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
        m = self._manager
        if not m.ready:
            return
        # ``_live`` is keyed by *run*, so the busy check has to read the run's own
        # status and the teardown has to be run-scoped: tearing down by agent id
        # would take out every other live execution of that agent as collateral.
        for run_id in list(m._live):
            run = await m._run(run_id)
            if run is not None and run.status in {"running", "needs_approval", "pending"}:
                continue
            await m._teardown_run(run_id, forget=False)
            if run is not None:
                m._auth_announced.pop(run.agent_id, None)

    async def teardown_run(self, run_id: int, *, forget: bool = False) -> None:
        """Disconnect a single run's live SDK session and drop its runtime state.

        ``forget`` additionally clears the run's *agent-level* timeline (memory
        cache and archive). That's deliberately broader than the run — the
        archive is per-agent — and is only used when the agent itself is going
        away or its conversation is being erased wholesale.
        """
        m = self._manager
        live = m._live.pop(run_id, None)
        m._live_locks.pop(run_id, None)
        if live is not None:
            with contextlib.suppress(Exception):
                await live.sdk_session.disconnect()
        run = await m._run(run_id)
        agent_id = run.agent_id if run is not None else None
        if agent_id is not None and m._agent_runs.get(agent_id) == run_id:
            m._agent_runs.pop(agent_id, None)
        if not forget or agent_id is None:
            return
        m._events.pop(agent_id, None)
        m._loaded.discard(agent_id)
        m._event_locks.pop(agent_id, None)
        m._auth_announced.pop(agent_id, None)
        m._transcript.bump_epoch(agent_id)
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
        m = self._manager
        run_ids: list[int] = []
        async with SessionLocal() as session:
            run_ids = [
                r
                for r in (
                    await session.execute(select(AgentRun.id).where(AgentRun.agent_id == agent_id))
                )
                .scalars()
                .all()
                if r in m._live or r in m._live_locks
            ]
        current = m._agent_runs.get(agent_id)
        if current is not None and current not in run_ids:
            run_ids.append(current)
        for run_id in run_ids:
            await m._teardown_run(run_id)
        if forget:
            m._events.pop(agent_id, None)
            m._loaded.discard(agent_id)
            m._event_locks.pop(agent_id, None)
            m._auth_announced.pop(agent_id, None)
            m._agent_runs.pop(agent_id, None)
            m._transcript.bump_epoch(agent_id)
            async with SessionLocal() as session:
                await session.execute(
                    delete(AgentEventRecord).where(AgentEventRecord.agent_session_id == agent_id)
                )
                await session.commit()
