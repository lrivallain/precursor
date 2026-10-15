"""Approval policies for chat and topic tool calls.

A conversation runs under one of three policies (its own ``approval_policy``,
else the global ``topic_approval_policy`` / ``chat_approval_policy``):

* ``manual`` — every tool call waits for the user.
* ``balanced`` — read-only tools run; anything that may change data waits.
* ``autonomous`` — nothing is gated.

Unless the policy is ``manual`` the model also gets a ``request_approval`` tool
to ask before an action it judges risky, whatever the tool's own annotations say.

A gated call *parks* the turn: the assistant row that carries it is saved, the
call becomes a ``tool_approvals`` row, and the loop stops. The row survives a
reload; once every approval is decided the resume endpoint runs the approved
calls, writes the tool results and lets the model continue.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from precursor.backend.models import Chat, Message, MessageRole, Topic
from precursor.backend.models.tool_approval import (
    APPROVAL_APPROVED,
    APPROVAL_CANCELLED,
    APPROVAL_DENIED,
    APPROVAL_KIND_INTENT,
    APPROVAL_KIND_TOOL,
    APPROVAL_PENDING,
    APPROVAL_SCOPE_CONVERSATION,
    APPROVAL_SCOPE_ONCE,
    ToolApproval,
)
from precursor.backend.services.app_settings import (
    CONVERSATION_APPROVAL_POLICIES,
    resolve_chat_approval_policy,
    resolve_topic_approval_policy,
)
from precursor.backend.services.llm.base import ToolDef
from precursor.backend.services.mcp.client import MCPToolDef
from precursor.backend.services.workspaces import WORKSPACE_WRITE_TOOLS

ContainerKind = Literal["topic", "chat"]

REQUEST_APPROVAL_TOOL = "request_approval"

POLICY_MANUAL = "manual"
POLICY_BALANCED = "balanced"
POLICY_AUTONOMOUS = "autonomous"

# Built-in servers are audited by hand: the tools listed here change something
# (workspace files, notes, schedules, memories, state, outbound requests, shell);
# every other tool of that server only reads. A server missing from this table
# is third-party, so its own ``readOnlyHint`` decides and silence means "ask".
_BUILTIN_MUTATING: dict[str, frozenset[str]] = {
    "precursor": WORKSPACE_WRITE_TOOLS
    | frozenset(
        {
            "post_message",
            "append_note",
            "create_side_chat",
            "create_schedule",
            "set_schedule_enabled",
            "run_schedule_now",
            "set_reminder",
            "cancel_reminder",
            "store_memory",
            "update_memory",
            "state_set",
            "state_delete",
            "workflow_state_set",
            "workflow_state_delete",
        }
    ),
    "fetch": frozenset({"http_request"}),
    "drawio": frozenset({"create_diagram", "write_diagram_xml"}),
    "cmd-runner": frozenset({"run_command", "run_script"}),
}


def is_read_only(server: str, raw_name: str, tool: MCPToolDef | None = None) -> bool:
    """Whether a call can't change anything, so ``balanced`` lets it through."""
    mutating = _BUILTIN_MUTATING.get(server)
    if mutating is not None:
        return raw_name not in mutating
    return tool is not None and tool.read_only is True


@dataclass(frozen=True, slots=True)
class ApprovalGate:
    """What the tool loop consults before running each call."""

    policy: str = POLICY_AUTONOMOUS
    # Qualified tool names the user allowed for the rest of the conversation.
    allowed: frozenset[str] = frozenset()

    @property
    def offers_intent(self) -> bool:
        return self.policy != POLICY_MANUAL

    def reason_for(
        self, qualified: str, server: str, raw_name: str, tool: MCPToolDef | None
    ) -> str | None:
        """Why this call must wait for the user, or ``None`` to run it now."""
        if qualified in self.allowed:
            return None
        if self.policy == POLICY_MANUAL:
            return "Manual policy: every tool call needs your approval."
        if self.policy == POLICY_BALANCED and not is_read_only(server, raw_name, tool):
            return "This tool may change data, so the balanced policy asks first."
        return None


REQUEST_APPROVAL_DEF = ToolDef(
    name=REQUEST_APPROVAL_TOOL,
    description=(
        "Ask the user for permission before you do something risky, then wait for "
        "the answer. Use it before an action that is hard to undo, deletes or "
        "overwrites data, sends something to other people, spends money, or goes "
        "beyond what the user asked for. Do not use it for ordinary reads."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "What you are about to do, in one sentence.",
            },
            "reason": {"type": "string", "description": "Why it is needed."},
            "risk": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "How much harm it could do if it is wrong.",
            },
        },
        "required": ["action"],
    },
)

APPROVAL_PROMPT = (
    "Approvals: you have a `request_approval` tool. Call it before any step that "
    "is destructive, irreversible, leaves this conversation (messages, requests "
    "or commands that affect others), or exceeds what the user asked. State the "
    "action and why. Continue only if the answer says it was approved; if it was "
    "denied, do not do it and do not retry it unless the user asks."
)

STOPPED_BY_USER_MOVE_ON = "Not run: the user moved on before approving this action."


def intent_result(approval: ToolApproval) -> str:
    """The tool result a decided ``request_approval`` hands back to the model."""
    if approval.status == APPROVAL_APPROVED:
        return "The user approved this. Proceed."
    if approval.status == APPROVAL_DENIED:
        note = f" Their note: {approval.feedback.strip()}" if approval.feedback else ""
        return f"The user denied this.{note} Do not do it, and do not retry it unless asked."
    return STOPPED_BY_USER_MOVE_ON


def denied_tool_result(approval: ToolApproval) -> str:
    """The tool result for a gated call that was denied or abandoned."""
    if approval.status == APPROVAL_DENIED:
        note = f" Their note: {approval.feedback.strip()}" if approval.feedback else ""
        return (
            f"The user denied this call to '{approval.tool_name}'.{note} "
            "Do not retry it unless the user asks."
        )
    return STOPPED_BY_USER_MOVE_ON


def _container_fk(kind: ContainerKind) -> InstrumentedAttribute[int | None]:
    return ToolApproval.topic_id if kind == "topic" else ToolApproval.chat_id


def _owner(kind: ContainerKind, container_id: int) -> dict[str, int]:
    return {"topic_id": container_id} if kind == "topic" else {"chat_id": container_id}


# -- Policy ----------------------------------------------------------------


async def resolve_policy(
    session: AsyncSession, *, topic_id: int | None = None, chat_id: int | None = None
) -> str:
    """The conversation's own policy, else the global default."""
    own: str | None = None
    if topic_id is not None:
        topic = await session.get(Topic, topic_id)
        own = topic.approval_policy if topic else None
    elif chat_id is not None:
        chat = await session.get(Chat, chat_id)
        own = chat.approval_policy if chat else None
    if own in CONVERSATION_APPROVAL_POLICIES:
        return own
    if chat_id is not None:
        return await resolve_chat_approval_policy(session)
    return await resolve_topic_approval_policy(session)


async def build_gate(session: AsyncSession, kind: ContainerKind, container_id: int) -> ApprovalGate:
    """The gate for a conversation's next turn (policy plus per-conversation allows)."""
    policy = await resolve_policy(session, **_owner(kind, container_id))
    rows = await session.execute(
        select(ToolApproval.tool_name).where(
            _container_fk(kind) == container_id,
            ToolApproval.kind == APPROVAL_KIND_TOOL,
            ToolApproval.status == APPROVAL_APPROVED,
            ToolApproval.scope == APPROVAL_SCOPE_CONVERSATION,
        )
    )
    return ApprovalGate(policy=policy, allowed=frozenset(rows.scalars().all()))


# -- Persistence -----------------------------------------------------------


@dataclass(slots=True)
class PendingCall:
    """A call the loop parked instead of running."""

    call_id: str
    tool_name: str
    server: str
    raw_tool: str
    arguments: str
    kind: str
    reason: str | None = None


def intent_pending(call_id: str, arguments: str) -> PendingCall:
    """Build the pending entry for a model-issued ``request_approval``."""
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    reason = parsed.get("reason")
    return PendingCall(
        call_id=call_id,
        tool_name=REQUEST_APPROVAL_TOOL,
        server="",
        raw_tool=REQUEST_APPROVAL_TOOL,
        arguments=arguments or "{}",
        kind=APPROVAL_KIND_INTENT,
        reason=reason if isinstance(reason, str) and reason.strip() else None,
    )


async def record_pending(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    assistant_message_id: int | None,
    calls: Iterable[PendingCall],
) -> list[ToolApproval]:
    rows = [
        ToolApproval(
            assistant_message_id=assistant_message_id,
            tool_call_id=c.call_id,
            tool_name=c.tool_name,
            server=c.server,
            raw_tool=c.raw_tool,
            arguments=c.arguments,
            kind=c.kind,
            reason=c.reason,
            status=APPROVAL_PENDING,
            scope=APPROVAL_SCOPE_ONCE,
            executed=False,
            **_owner(kind, container_id),
        )
        for c in calls
    ]
    session.add_all(rows)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


async def list_approvals(
    session: AsyncSession, kind: ContainerKind, container_id: int, *, open_only: bool = True
) -> list[ToolApproval]:
    """A conversation's approvals, oldest first; ``open_only`` skips executed ones."""
    stmt = select(ToolApproval).where(_container_fk(kind) == container_id)
    if open_only:
        stmt = stmt.where(ToolApproval.executed.is_(False))
    rows = await session.execute(stmt.order_by(ToolApproval.id))
    return list(rows.scalars().all())


async def decide(
    session: AsyncSession,
    kind: ContainerKind,
    container_id: int,
    approval_id: int,
    *,
    approve: bool,
    scope: str = APPROVAL_SCOPE_ONCE,
    feedback: str | None = None,
) -> ToolApproval:
    """Record the user's answer to one pending approval."""
    row = await session.get(ToolApproval, approval_id)
    owner = None if row is None else (row.topic_id if kind == "topic" else row.chat_id)
    if row is None or owner != container_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    if row.status != APPROVAL_PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "This approval was already decided")
    row.status = APPROVAL_APPROVED if approve else APPROVAL_DENIED
    # "Always allow" only makes sense for a real tool; an intent is per-action.
    row.scope = (
        scope
        if approve and row.kind == APPROVAL_KIND_TOOL and scope in (APPROVAL_SCOPE_CONVERSATION,)
        else APPROVAL_SCOPE_ONCE
    )
    row.feedback = (feedback or "").strip() or None
    row.decided_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return row


async def decided_unexecuted(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> tuple[list[ToolApproval], int]:
    """Decided calls whose tool rows are still unwritten, plus how many still wait."""
    rows = await list_approvals(session, kind, container_id)
    waiting = sum(1 for r in rows if r.status == APPROVAL_PENDING)
    return [r for r in rows if r.status != APPROVAL_PENDING], waiting


async def mark_executed(approval_id: int) -> None:
    from precursor.backend.db import SessionLocal

    async with SessionLocal() as ws:
        row = await ws.get(ToolApproval, approval_id)
        if row is not None:
            row.executed = True
            await ws.commit()


async def pending_counts(
    session: AsyncSession, kind: ContainerKind, container_ids: Sequence[int] | None = None
) -> dict[int, int]:
    """Map container id -> approvals still waiting on the user."""
    fk = _container_fk(kind)
    stmt = (
        select(fk, func.count(ToolApproval.id))
        .where(fk.is_not(None), ToolApproval.status == APPROVAL_PENDING)
        .group_by(fk)
    )
    if container_ids is not None:
        stmt = stmt.where(fk.in_(container_ids))
    return {
        row[0]: int(row[1]) for row in (await session.execute(stmt)).all() if row[0] is not None
    }


async def abandon_open(session: AsyncSession, kind: ContainerKind, container_id: int) -> int:
    """Settle every unexecuted approval so the transcript stays well-formed.

    Called when the user moves on (sends a new message, rewinds): each open call
    gets a "not run" tool row right after its assistant turn, which hydration
    needs to keep that turn instead of dropping it as an orphan. Returns how
    many were settled.
    """
    rows = await list_approvals(session, kind, container_id)
    if not rows:
        return 0
    for row in rows:
        if row.status == APPROVAL_PENDING:
            row.status = APPROVAL_CANCELLED
            row.decided_at = datetime.now(UTC)
        meta = {
            "tool_call_id": row.tool_call_id,
            "name": row.tool_name,
            "arguments": row.arguments,
            "is_error": True,
        }
        session.add(
            Message(
                role=MessageRole.TOOL,
                content=(
                    intent_result(row)
                    if row.kind == APPROVAL_KIND_INTENT
                    else denied_tool_result(row)
                ),
                tool_calls=json.dumps(meta),
                **_owner(kind, container_id),
            )
        )
        row.executed = True
    await session.commit()
    return len(rows)


async def delete_for_container(
    session: AsyncSession, kind: ContainerKind, container_id: int
) -> None:
    """Drop a conversation's approvals (SQLite doesn't enforce the cascade)."""
    await session.execute(delete(ToolApproval).where(_container_fk(kind) == container_id))


async def delete_after(
    session: AsyncSession, kind: ContainerKind, container_id: int, message_id: int
) -> None:
    """Drop approvals raised by assistant rows newer than ``message_id``."""
    await session.execute(
        delete(ToolApproval).where(
            _container_fk(kind) == container_id, ToolApproval.assistant_message_id > message_id
        )
    )


async def delete_for_messages(session: AsyncSession, message_ids: Any) -> None:
    """Drop approvals raised by these assistant rows (ids or a ``select`` of ids)."""
    await session.execute(
        delete(ToolApproval).where(ToolApproval.assistant_message_id.in_(message_ids))
    )


def serialize(row: ToolApproval) -> dict[str, Any]:
    """The JSON shape the SSE ``approval_required`` event and the API share."""
    return {
        "id": row.id,
        "topic_id": row.topic_id,
        "chat_id": row.chat_id,
        "assistant_message_id": row.assistant_message_id,
        "tool_call_id": row.tool_call_id,
        "tool_name": row.tool_name,
        "server": row.server,
        "raw_tool": row.raw_tool,
        "arguments": row.arguments,
        "kind": row.kind,
        "reason": row.reason,
        "status": row.status,
        "scope": row.scope,
        "feedback": row.feedback,
        "executed": row.executed,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }
