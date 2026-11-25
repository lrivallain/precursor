"""Self wakeup tools: pause a conversation and wake it up later (``services/wakeups``).

Topic and chat turns get these as built-in tools of the turn
(``services/wakeup_tools``); this group serves agents, which reach Precursor
through this server and name their conversation by its id.
"""

from __future__ import annotations

from typing import Any

from precursor.backend.db import SessionLocal
from precursor.backend.services import wakeup_tools, wakeups
from precursor.backend.services.mcp.precursor_tools._core import _GATED, _section_enabled, mcp

_SECTION = "wakeups"


async def _run(conversation_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not await _section_enabled(_SECTION):
        return {"error": _GATED.format(section=_SECTION)}
    async with SessionLocal() as session:
        target = await wakeups.resolve_public_id(session, conversation_id)
    if target is None:
        return {"error": f"No topic, chat or agent with id {conversation_id!r}."}
    return await wakeup_tools.run_wakeup_tool(*target, name, arguments)


@mcp.tool()
async def schedule_wakeup(
    conversation_id: str,
    prompt: str,
    delay_seconds: float | None = None,
    at: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Pause a conversation and wake yourself up later.

    Use it when you must wait for something outside the conversation (a CI run,
    a deploy, a long job, a reply) before you can go on. At the given time,
    ``prompt`` is sent back to you as a new message, so write it as a note to
    your future self: what you were waiting for and what to check next.
    ``conversation_id`` is your own id (an agent's is in its instructions).
    Give exactly one of ``delay_seconds`` (preferred, 10 s to 30 days) or ``at``
    (ISO 8601; no offset means UTC). ``reason`` is shown to the user while the
    conversation is paused. After scheduling, end your turn. One wakeup per
    conversation: scheduling again replaces it.
    """
    arguments = {"prompt": prompt, "delay_seconds": delay_seconds, "at": at, "reason": reason}
    return await _run(conversation_id, wakeup_tools.SCHEDULE_WAKEUP, arguments)


@mcp.tool()
async def cancel_wakeup(conversation_id: str) -> dict[str, Any]:
    """Cancel the pending wakeup of a conversation (agent, topic or chat id)."""
    return await _run(conversation_id, wakeup_tools.CANCEL_WAKEUP, {})
