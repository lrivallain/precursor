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
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentSession, Message, MessageRole, Topic
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agents import runtime
from precursor.backend.services.app_settings import (
    resolve_agents_approval_policy,
    resolve_agents_enabled,
)
from precursor.backend.services.events import (
    publish_agent_changed,
    publish_message_changed,
    publish_message_changed_chat,
)

logger = logging.getLogger(__name__)

# Cap how long we wait for the out-of-process runtime to come up so a stuck or
# unauthenticated CLI can't block app startup or a settings save indefinitely.
_START_TIMEOUT_SECONDS = 30.0


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


class AgentManager:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._ready = False
        self._live: dict[int, _LiveSession] = {}
        # Durable per-agent timeline. The SDK's ``get_events`` is per-connection
        # (a resumed session only replays ``SessionStartData``), so we archive
        # every streamed event here. This survives ``teardown_session`` — e.g.
        # when a topic is linked — so the workflow view isn't wiped. Cleared only
        # when the agent is deleted, or lost on process restart (acceptable: the
        # plan keeps history out of the DB).
        self._events: dict[int, list[AgentEvent]] = {}
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
        context = await self._topic_context(agent)
        if context:
            # Append (don't replace) so the agent keeps its base instructions but
            # always knows which topic it's bound to and how to read/write it.
            kwargs["system_message"] = {"mode": "append", "content": context}

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
        live = self._live.get(agent_id)
        events = list(self._events.get(agent_id, []))
        if not events:
            # No archived stream yet (e.g. first load after a resume). Fall back
            # to whatever the live session can replay.
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

    async def list_models(self) -> list[dict[str, str]]:
        """Return the runtime's available models (``id``/``name``), or empty.

        Used to populate the Settings default-model picker. The SDK caches the
        result after the first call, so this is cheap to poll.
        """
        if not self._ready or self._client is None:
            return []
        try:
            models = await self._client.list_models()
        except Exception:
            logger.debug("list_models failed", exc_info=True)
            return []
        out: list[dict[str, str]] = []
        for m in models or []:
            mid = getattr(m, "id", None)
            if not mid:
                continue
            out.append({"id": str(mid), "name": str(getattr(m, "name", None) or mid)})
        return out

    def _make_permission_handler(self, agent_id: int) -> Any:
        async def handler(request: Any, invocation: Any) -> Any:
            # The default approval policy decides how much we gate. ``autonomous``
            # approves everything; ``balanced`` (default) auto-approves read-only
            # intents (reads, URL fetches, read-only MCP) and our own precursor
            # MCP calls; ``manual`` asks for everything. Anything not auto-approved
            # is parked for explicit user approval.
            policy = await self._approval_policy()
            if policy == "autonomous":
                return self._approve_once()
            if policy != "manual" and self._should_auto_approve(request):
                return self._approve_once()
            info = self._describe_permission(request)
            live = self._live.get(agent_id)
            # Honour a prior "approve for session" for the same action.
            if live is not None and self._signature(info) in live.session_approvals:
                return self._approve_once()
            return await self._park_permission(agent_id, request, info)

        return handler

    async def _approval_policy(self) -> str:
        async with SessionLocal() as session:
            return await resolve_agents_approval_policy(session)

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

    async def _handle_event(self, agent_id: int, event: Any) -> None:
        # Archive every event so the timeline persists across session teardown
        # (e.g. on topic link) and resume, where the SDK would otherwise drop it.
        self._events.setdefault(agent_id, []).append(self._normalise(event))

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
        """Post the agent exchange into the linked container when a task finishes.

        Posts the initial **prompt** (as a user turn) and the agent's **answer**
        (as an assistant turn), both tagged with ``agent_session_id`` so the UI
        renders an "agent exchange" badge linking back to ``/agents/{id}``. Like
        the reminder ticker, the discussion goes unread + notifies.

        Guarded to post **once** per agent (the initial task exchange): later
        idles from follow-up turns are skipped, detected via an existing
        agent-tagged message in the container.
        """
        if agent.topic_id is None and agent.chat_id is None:
            return
        from sqlalchemy import select

        answer = (agent.result_summary or "").strip() or "Agent task finished."
        prompt = (agent.task_prompt or "").strip()
        async with SessionLocal() as session:
            already = await session.scalar(
                select(Message.id).where(Message.agent_session_id == agent.id).limit(1)
            )
            if already is not None:
                return
            if prompt:
                session.add(
                    Message(
                        topic_id=agent.topic_id,
                        chat_id=agent.chat_id,
                        role=MessageRole.USER,
                        content=prompt,
                        agent_session_id=agent.id,
                    )
                )
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.ASSISTANT,
                    content=answer,
                    agent_session_id=agent.id,
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
        # Capture tool I/O so the UI can show "what was done" on demand.
        extra: dict[str, Any] = {}
        for attr in ("arguments", "input", "result", "output", "server_name"):
            val = getattr(data, attr, None)
            if val is None:
                continue
            if attr in ("result", "output"):
                val = self._unwrap_result(val)
            extra[attr] = val if isinstance(val, str) else self._jsonify(val)
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
