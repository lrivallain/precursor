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
  agent can read topic context and post results back (``post_message``).
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
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentSession, Message, MessageRole
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents import runtime
from precursor.backend.services.app_settings import resolve_agents_enabled
from precursor.backend.services.events import (
    publish_agent_changed,
    publish_message_changed,
    publish_message_changed_chat,
)

logger = logging.getLogger(__name__)

# Tool kinds (Copilot permission-request variants) auto-approved without asking.
# Reads, URL fetches and MCP calls are allowed; writes / shell / memory are
# parked for explicit approval. MCP calls to our own 'precursor' server are
# always allowed (that's the notify-back path).
_AUTO_APPROVE_KINDS = {"read", "url", "mcp"}

# Cap how long we wait for the out-of-process runtime to come up so a stuck or
# unauthenticated CLI can't block app startup or a settings save indefinitely.
_START_TIMEOUT_SECONDS = 30.0


@dataclass
class _LiveSession:
    """A live SDK session handle plus its pending permission requests."""

    sdk_session: Any
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)


class AgentManager:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._ready = False
        self._live: dict[int, _LiveSession] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()

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

    # ------------------------------------------------------------------ helpers

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
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
        config: Any = sdk.MCPStdioServerConfig(
            type="stdio",
            command=sys.executable,
            args=["-m", "precursor.backend.services.mcp.precursor_server"],
            env=dict(os.environ),
        )
        return {"precursor": config}

    # ------------------------------------------------------------------ sessions

    async def _ensure_live(self, agent: AgentSession) -> _LiveSession:
        """Return the live SDK session for ``agent``, creating/resuming it."""
        self._require_ready()
        live = self._live.get(agent.id)
        if live is not None:
            return live

        assert self._client is not None
        kwargs: dict[str, Any] = {
            "model": agent.model or get_settings().agents_default_model,
            "on_permission_request": self._make_permission_handler(agent.id),
            "streaming": False,
        }
        if agent.copilot_session_id:
            kwargs["session_id"] = agent.copilot_session_id
        mcp = self._precursor_mcp_config()
        if mcp:
            kwargs["mcp_servers"] = mcp

        sdk_session = await self._client.create_session(**kwargs)
        live = _LiveSession(sdk_session=sdk_session)
        self._live[agent.id] = live

        # Wire the event stream. The SDK invokes this synchronously; defer the
        # async work (DB + bus) onto the loop.
        sdk_session.on(lambda event: self._spawn(self._handle_event(agent.id, event)))

        # Persist the resume handle the first time round.
        sid = getattr(sdk_session, "id", None) or getattr(sdk_session, "session_id", None)
        if sid and not agent.copilot_session_id:
            await self._patch(agent.id, copilot_session_id=str(sid))
        return live

    async def start_task(self, agent_id: int) -> None:
        agent = await self._load(agent_id)
        if agent is None:
            return
        live = await self._ensure_live(agent)
        await self._patch(agent_id, status="running", error=None)
        await publish_agent_changed(
            agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
        )
        await live.sdk_session.send(agent.task_prompt)

    async def send_message(self, agent_id: int, text: str) -> None:
        agent = await self._load(agent_id)
        if agent is None:
            return
        live = await self._ensure_live(agent)
        await self._patch(agent_id, status="running")
        await publish_agent_changed(
            agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
        )
        await live.sdk_session.send(text)

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
        fut.set_result(self._decision(decision))
        return True

    async def get_events(self, agent_id: int) -> list[AgentEvent]:
        """Return the normalised event history for the workflow timeline."""
        live = self._live.get(agent_id)
        if live is None:
            agent = await self._load(agent_id)
            if agent is None or not agent.copilot_session_id:
                return []
            live = await self._ensure_live(agent)
        try:
            raw = await live.sdk_session.get_events()
        except Exception:
            logger.debug("get_events failed for agent %s", agent_id, exc_info=True)
            return []
        return [self._normalise(ev) for ev in raw or []]

    async def teardown_session(self, agent_id: int) -> None:
        """Disconnect a live session (e.g. before deleting the row)."""
        live = self._live.pop(agent_id, None)
        if live is not None:
            with contextlib.suppress(Exception):
                await live.sdk_session.disconnect()

    # ------------------------------------------------------------------ permissions

    def _make_permission_handler(self, agent_id: int) -> Any:
        async def handler(request: Any, invocation: Any) -> Any:
            kind = str(getattr(request, "kind", "") or "").lower()
            server = str(getattr(request, "server_name", "") or "")
            read_only = bool(getattr(request, "read_only", False))
            # Always allow our own precursor MCP calls (the notify-back path),
            # plus reads / url fetches / read-only MCP.
            if server == "precursor" or kind in _AUTO_APPROVE_KINDS or read_only:
                return self._approve_once()
            # Gate the rest: park a future and surface needs_approval.
            return await self._park_permission(agent_id, request)

        return handler

    async def _park_permission(self, agent_id: int, request: Any) -> Any:
        live = self._live.get(agent_id)
        if live is None:
            return self._reject("session gone")
        request_id = str(getattr(request, "tool_call_id", "") or id(request))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        live.pending[request_id] = fut
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

    def _approve_once(self) -> Any:
        sdk = runtime.load_sdk()
        return sdk.PermissionDecisionApproveOnce()

    def _reject(self, feedback: str) -> Any:
        sdk = runtime.load_sdk()
        return sdk.PermissionDecisionReject(feedback=feedback)

    def _decision(self, decision: str) -> Any:
        sdk = runtime.load_sdk()
        if decision == "approve-always":
            with contextlib.suppress(Exception):
                return sdk.PermissionDecisionApproveForSession()
            return sdk.PermissionDecisionApproveOnce()
        if decision == "deny":
            return sdk.PermissionDecisionReject(feedback="Denied by user")
        return sdk.PermissionDecisionApproveOnce()

    # ------------------------------------------------------------------ events

    async def _handle_event(self, agent_id: int, event: Any) -> None:
        data = getattr(event, "data", event)
        name = type(data).__name__
        now = datetime.now(UTC)
        patch: dict[str, Any] = {"last_activity_at": now}

        if name == "AssistantMessageData":
            content = getattr(data, "content", None)
            if content:
                patch["result_summary"] = str(content)[:2000]
        elif name in ("SessionIdleData", "SystemNotificationAgentIdle"):
            agent = await self._load(agent_id)
            if agent is not None and agent.status not in ("needs_approval", "cancelled"):
                patch["status"] = "idle"
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

    async def _notify_back(self, agent: AgentSession) -> None:
        """Post a system message to the linked container when a task finishes.

        Mirrors the reminder ticker: the discussion goes unread + notifies. The
        agent may *also* have posted richer content itself via ``post_message``.
        """
        if agent.topic_id is None and agent.chat_id is None:
            return
        summary = (agent.result_summary or "").strip() or "Agent task finished."
        content = f"🤖 Agent **{agent.title}** finished.\n\n{summary}"
        async with SessionLocal() as session:
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.SYSTEM,
                    content=content,
                )
            )
            await session.commit()
        if agent.topic_id is not None:
            await publish_message_changed(agent.topic_id)
        elif agent.chat_id is not None:
            await publish_message_changed_chat(agent.chat_id)

    def _normalise(self, event: Any) -> AgentEvent:
        """Map a raw SDK event onto the workflow-timeline shape."""
        data = getattr(event, "data", event)
        name = type(data).__name__
        text = getattr(data, "content", None) or getattr(data, "text", None)
        tool_name = getattr(data, "tool_name", None) or getattr(data, "name", None)
        kind = _EVENT_KINDS.get(name, name)
        tool_status: str | None = None
        if "ToolRequest" in name or kind == "tool_call":
            tool_status = "running"
        elif "ToolResult" in name or kind == "tool_result":
            tool_status = "error" if getattr(data, "is_error", False) else "done"
        return AgentEvent(
            kind=kind,
            text=str(text) if text is not None else None,
            tool_name=str(tool_name) if tool_name else None,
            tool_status=tool_status,
            request_id=getattr(data, "tool_call_id", None),
        )

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
    "AssistantReasoningDeltaData": "reasoning",
    "AssistantTurnStartData": "turn_start",
    "AssistantTurnEndData": "turn_end",
    "AssistantUsageData": "usage",
    "SessionIdleData": "idle",
    "AbortData": "aborted",
}

_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
