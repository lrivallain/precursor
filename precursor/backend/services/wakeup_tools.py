"""The wakeup tools every topic and chat turn gets (``services/wakeups``).

They are local tools of the turn rather than Precursor MCP tools: they need no
server switched on, and they act on the conversation the turn belongs to, so
the model never has to name it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from precursor.backend.db import SessionLocal
from precursor.backend.schemas.wakeup import WakeupKind
from precursor.backend.services import wakeups
from precursor.backend.services.llm.base import ToolDef

SCHEDULE_WAKEUP = "schedule_wakeup"
CANCEL_WAKEUP = "cancel_wakeup"

SCHEDULE_DESCRIPTION = (
    "Pause this conversation and wake yourself up later. Use it when you must wait for "
    "something outside the conversation (a CI run, a deploy, a long job, a reply) before "
    "you can go on. At the given time, `prompt` is sent back to you as a new message, with "
    "the conversation's history, so write it as a note to your future self: what you were "
    "waiting for and what to check or do next. Give exactly one of `delay_seconds` "
    "(preferred) or `at` (ISO 8601; a time without an offset is UTC). After scheduling, "
    "end your turn with a short note telling the user what you are waiting for. A "
    "conversation holds one wakeup; scheduling again replaces it."
)

SCHEDULE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The message you will be woken up with.",
        },
        "delay_seconds": {
            "type": "number",
            "description": "Seconds from now (10 seconds to 30 days).",
        },
        "at": {"type": "string", "description": "When to wake up, ISO 8601."},
        "reason": {
            "type": "string",
            "description": "What you are waiting for, shown to the user while paused.",
        },
    },
    "required": ["prompt"],
}

CANCEL_DESCRIPTION = "Cancel this conversation's pending wakeup, if it has one."

TOOL_DEFS: tuple[ToolDef, ...] = (
    ToolDef(SCHEDULE_WAKEUP, SCHEDULE_DESCRIPTION, SCHEDULE_PARAMETERS),
    ToolDef(CANCEL_WAKEUP, CANCEL_DESCRIPTION, {"type": "object", "properties": {}}),
)

Runner = Callable[[Any], Awaitable[str]]


def _number(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


async def run_wakeup_tool(
    kind: WakeupKind, target_id: int, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Run one wakeup tool against a conversation; errors come back as ``{"error": ...}``."""
    async with SessionLocal() as session:
        if name == CANCEL_WAKEUP:
            return {"cancelled": await wakeups.cancel(session, kind, target_id)}
        try:
            wake_at = wakeups.resolve_wake_at(
                delay_seconds=_number(arguments.get("delay_seconds")),
                at=str(arguments["at"]) if arguments.get("at") else None,
            )
            wakeup = await wakeups.schedule(
                session,
                kind,
                target_id,
                wake_at=wake_at,
                prompt=str(arguments.get("prompt") or ""),
                reason=str(arguments.get("reason") or "") or None,
            )
        except (wakeups.WakeupError, TypeError, ValueError) as exc:
            return {"error": str(exc)}
        return wakeups.as_dict(wakeup)


def local_tools(kind: Literal["topic", "chat"], container_id: int) -> tuple[list[ToolDef], Runner]:
    """The wakeup tools of a topic or chat turn, and the runner ``run_tool_loop`` calls."""

    async def run(call: Any) -> str:
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": "Arguments are not valid JSON."})
        if not isinstance(arguments, dict):
            arguments = {}
        result = await run_wakeup_tool(kind, container_id, call.name, arguments)
        return json.dumps(result)

    return list(TOOL_DEFS), run


def loop_kwargs(kind: Literal["topic", "chat"], container_id: int) -> dict[str, Any]:
    """``run_tool_loop`` keyword arguments that give a turn the wakeup tools."""
    tools, run = local_tools(kind, container_id)
    return {"extra_tools": tools, "run_extra": run}
