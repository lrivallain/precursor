"""Permission requests for agent sessions.

The pure helpers describe an SDK permission request for the UI and decide
auto-approval. ``PermissionBroker`` owns the stateful side on behalf of
``AgentManager``: the SDK permission handler, parking a request until the user
answers, resolving/listing/revoking "approve for session" grants, and the
approval-policy lookup. The per-run state it reads (``_live``) stays on the
manager; this module must not import ``manager`` at runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from precursor.backend.db import SessionLocal
from precursor.backend.models import AgentRun, AgentSession
from precursor.backend.services.agents import runtime
from precursor.backend.services.app_settings import (
    AGENTS_APPROVAL_POLICIES,
    DEFAULT_AGENTS_APPROVAL_POLICY,
    resolve_agents_approval_policy,
)
from precursor.backend.services.events import publish_agent_changed

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager

logger = logging.getLogger(__name__)


def permission_signature(info: dict[str, Any]) -> tuple[str, str | None]:
    """A stable key for "approve for session": the action and its target."""
    target = (
        info.get("command")
        or info.get("path")
        or info.get("url")
        or info.get("tool")
        or info.get("server")
    )
    return (str(info.get("type", "tool")), str(target) if target else None)


def should_auto_approve(request: Any) -> bool:
    name = type(request).__name__
    if name in ("PermissionRequestRead", "PermissionRequestUrl"):
        return True
    if name == "PermissionRequestMcp":
        server = str(getattr(request, "server_name", "") or "")
        return server == "precursor" or bool(getattr(request, "read_only", False))
    return False


def describe_permission(request: Any) -> dict[str, Any]:
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


class PermissionBroker:
    def __init__(self, manager: AgentManager) -> None:
        # Held as a back-reference and read at call time, never captured as
        # bound methods, so a test patching the manager still takes effect.
        self._manager = manager

    def make_handler(self, run_id: int) -> Any:
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
                live = self._manager._live.get(run_id)
                policy = (live.approval_policy if live else None) or DEFAULT_AGENTS_APPROVAL_POLICY
                logger.info(
                    "run %s: permission handler hit — request=%s policy=%s live=%s",
                    run_id,
                    req_name,
                    policy,
                    live is not None,
                )
                if policy == "autonomous":
                    logger.info("run %s: %s auto-approved (autonomous)", run_id, req_name)
                    return self._manager._approve_once()
                if policy != "manual" and should_auto_approve(request):
                    logger.info("run %s: %s auto-approved (read-only)", run_id, req_name)
                    return self._manager._approve_once()
                info = describe_permission(request)
                # Honour a prior "approve for session" for the same action.
                if live is not None and permission_signature(info) in live.session_approvals:
                    logger.info("run %s: %s auto-approved (session grant)", run_id, req_name)
                    return self._manager._approve_once()
                logger.info(
                    "run %s: %s requires approval — parking (%s)",
                    run_id,
                    req_name,
                    info.get("title"),
                )
                return await self.park(run_id, request, info)
            except asyncio.CancelledError:
                raise
            except Exception:
                fallback = DEFAULT_AGENTS_APPROVAL_POLICY
                logger.exception(
                    "run %s: permission handler failed for %s; falling back to %r policy",
                    run_id,
                    req_name,
                    fallback,
                )
                # Don't silently deny in unattended modes — that's the bug we're
                # guarding against. Manual mode can't safely auto-approve, so emit
                # an explicit rejection the UI can show rather than a crash.
                if fallback != "manual":
                    return self._manager._approve_once()
                return self._manager._reject("permission handler error")

        return handler

    async def park(self, run_id: int, request: Any, info: dict[str, Any] | None = None) -> Any:
        live = self._manager._live.get(run_id)
        if live is None:
            logger.warning("run %s: cannot park permission — no live session; rejecting", run_id)
            return self._manager._reject("session gone")
        request_id = str(getattr(request, "tool_call_id", "") or id(request))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        live.pending[request_id] = fut
        live.pending_info[request_id] = {
            "request_id": request_id,
            **(info if info is not None else describe_permission(request)),
        }
        await self._manager._patch_run(run_id, status="needs_approval")
        loaded = await self._manager._load_run(run_id)
        agent = loaded[1] if loaded else None
        await publish_agent_changed(
            agent_session_id=agent.id if agent else 0,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
            agent_run_id=run_id,
        )
        try:
            return await fut
        finally:
            live.pending.pop(request_id, None)
            live.pending_info.pop(request_id, None)

    async def release_parked_turn(self, agent_id: int, live: Any) -> None:
        """Free a session parked on an unanswered permission before re-driving it.

        A turn that stopped at a permission gate is still *open*: the SDK is
        awaiting a decision that, by the time we're starting a new task, nobody is
        going to give. Sending the next prompt into that session just queues it
        behind the gate, so the "retry" burns its whole watchdog window without
        running anything and dies with the same stall it was meant to fix.

        Rejecting the pending futures lets the old turn unwind, and the abort
        stops whatever it does next, so the new prompt lands on an idle session.
        Nothing to do for a session that wasn't parked — the common case.
        """
        if not getattr(live, "pending", None):
            return
        logger.info(
            "agent %s: releasing %d unanswered permission request(s) before re-driving",
            agent_id,
            len(live.pending),
        )
        for fut in list(live.pending.values()):
            if not fut.done():
                fut.set_result(self._manager._reject("superseded by a new run"))
        with contextlib.suppress(Exception):
            await live.sdk_session.abort()

    async def resolve(self, agent_id: int, request_id: str, decision: str) -> bool:
        """Resolve a parked permission request. Returns True if one matched."""
        run = await self._manager._resolve_run(agent_id)
        live = self._manager._live.get(run.id) if run is not None else None
        if live is None or run is None:
            return False
        fut = live.pending.get(request_id)
        if fut is None or fut.done():
            return False
        if decision == "approve-always":
            # Remember the action for the rest of the session (enforced locally by
            # the permission handler) and record the grant for the Settings recap.
            info = live.pending_info.get(request_id, {})
            live.session_approvals.add(permission_signature(info))
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
        fut.set_result(self._manager._decision(decision))
        # The turn resumes, so the agent is working again. This *must* happen
        # here rather than at each call site: ``needs_approval`` is a sticky
        # status the idle handler deliberately skips (so a trailing idle can't
        # mask a genuinely parked agent), which means an agent left sitting in it
        # never reaches ``_on_idle`` — its turn finishes, the workflow is never
        # told, and the step stays "Running" forever.
        await self._manager._patch_run(run.id, status="running", blocked_question=None)
        agent = await self._manager._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
            agent_run_id=run.id,
        )
        return True

    async def list_grants(self) -> list[dict[str, Any]]:
        """Recap of active "approve for session" grants across live sessions.

        Grants are held per *run*; the recap is agent-addressed (that's the row
        the user recognises in Settings), so runs are resolved back to their
        agents here.
        """
        out: list[dict[str, Any]] = []
        run_ids = [rid for rid, live in self._manager._live.items() if live.grants]
        if not run_ids:
            return out
        async with SessionLocal() as session:
            runs = (
                (await session.execute(select(AgentRun).where(AgentRun.id.in_(run_ids))))
                .scalars()
                .all()
            )
        agent_by_run = {r.id: r.agent_id for r in runs}
        for run_id in run_ids:
            live = self._manager._live[run_id]
            agent_id = agent_by_run.get(run_id)
            if agent_id is None:
                continue
            for grant in live.grants:
                out.append({"agent_id": agent_id, "agent_run_id": run_id, **grant})
        out.sort(key=lambda g: g.get("at") or datetime.min.replace(tzinfo=UTC), reverse=True)
        return out

    async def reset(self) -> int:
        """Revoke all session grants by disconnecting every live session.

        Tearing the SDK sessions down drops their in-session approvals; they're
        recreated fresh (and will ask again) on next use. Returns the count of
        grants cleared.
        """
        cleared = sum(len(live.grants) for live in self._manager._live.values())
        for run_id in list(self._manager._live.keys()):
            await self._manager._teardown_run(run_id)
        return cleared


async def approval_policy(agent: AgentSession | None = None, run: AgentRun | None = None) -> str:
    # The executing run's snapshot wins (it froze the policy a workflow step
    # asked for), then a per-agent override, then the DB-backed global
    # setting. ``None``/unset at each level falls through.
    for source in (run, agent):
        override = getattr(source, "approval_policy", None)
        if override in AGENTS_APPROVAL_POLICIES:
            return str(override)
    try:
        async with SessionLocal() as session:
            return await resolve_agents_approval_policy(session)
    except Exception:
        fallback = DEFAULT_AGENTS_APPROVAL_POLICY
        logger.warning(
            "agent: approval-policy DB read failed; using in-memory default %r",
            fallback,
            exc_info=True,
        )
        return fallback


def approve_once() -> Any:
    return runtime.load_rpc().PermissionDecisionApproveOnce()


def reject(feedback: str) -> Any:
    return runtime.load_rpc().PermissionDecisionReject(feedback=feedback)


def decision(decision: str) -> Any:
    rpc = runtime.load_rpc()
    if decision == "deny":
        return rpc.PermissionDecisionReject(feedback="Denied by user")
    # Both approve-once and approve-for-session approve the *current* request
    # with the same SDK call. We don't emit PermissionDecisionApproveForSession
    # — its mandatory ``approval`` object for command/write prompts is what
    # triggers the runtime's "missing approval field" error. Session scope is
    # instead enforced by ``session_approvals`` in the permission handler.
    return rpc.PermissionDecisionApproveOnce()
