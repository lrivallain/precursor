"""System slash commands accepted inside an agent session.

Each handler takes ``(manager, agent_id, argument)`` and reaches state and
sibling operations through the manager at call time (``manager.clear_session``
etc.), so patching the manager in tests still takes effect. The session
operations behind ``/clear`` and ``/compact`` (and the scheduler's ``/run``
nudge) live here too. ``AgentManager``
exposes this registry as ``_COMMAND_HANDLERS`` and keeps ``run_command`` /
``supported_commands``. Must not import ``manager`` at runtime.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from precursor.backend.db import SessionLocal
from precursor.backend.services import roles as roles_service

if TYPE_CHECKING:
    from precursor.backend.services.agents.manager import AgentManager


async def cmd_rename(manager: AgentManager, agent_id: int, argument: str) -> None:
    title = " ".join(argument.split())[:200]
    if not title:
        raise ValueError("Usage: /rename <new title>")
    await manager._patch(agent_id, title=title)
    await manager._publish(agent_id)


async def cmd_archive(manager: AgentManager, agent_id: int, argument: str) -> None:
    agent = await manager._load(agent_id)
    if agent is not None and agent.archived_at is None:
        await manager._patch(agent_id, archived_at=datetime.now(UTC))
        await manager._publish(agent_id)


async def cmd_clear(manager: AgentManager, agent_id: int, argument: str) -> None:
    await manager.clear_session(agent_id)


async def cmd_compact(manager: AgentManager, agent_id: int, argument: str) -> None:
    await manager.compact(agent_id, argument)


async def cmd_role(manager: AgentManager, agent_id: int, argument: str) -> None:
    name = " ".join(argument.split())
    if not name:
        # The bare form opens the composer's role picker client-side, so it
        # normally never reaches here.
        raise ValueError("Usage: /role <name>")
    async with SessionLocal() as session:
        role = await roles_service.find_role(session, name)
    if role is None:
        raise ValueError(f'Unknown role "{name}". Manage roles in Settings → Roles.')
    # The default role is persisted as NULL, never by its own name.
    await manager._patch(agent_id, role=None if role.is_default else role.name)
    await manager._publish(agent_id)


async def cmd_memory_store(manager: AgentManager, agent_id: int, argument: str) -> None:
    from precursor.backend.services import memories as memory_service

    payload = memory_service.parse_store_arg(argument)
    async with SessionLocal() as session:
        await memory_service.create_memory(session, payload)


async def cmd_memory_update(manager: AgentManager, agent_id: int, argument: str) -> None:
    from precursor.backend.services import memories as memory_service

    memory_id, payload = memory_service.parse_update_arg(argument)
    async with SessionLocal() as session:
        try:
            await memory_service.update_memory(session, memory_id, payload)
        except LookupError as exc:
            raise ValueError(str(exc)) from exc


# A manual /compact is one summarising model call over the whole history.
_COMPACT_TIMEOUT_SECONDS = 300.0


async def clear_session(
    manager: AgentManager, agent_id: int, *, keep_id: bool = False, trigger: str = "manual"
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
    prior = await manager._resolve_run(agent_id)
    old_id = prior.copilot_session_id if prior is not None else None

    await manager.teardown_session(agent_id, forget=True)

    if keep_id and old_id and manager._client is not None:
        # Best-effort: a never-connected (pending) agent has nothing on disk.
        with contextlib.suppress(Exception):
            await manager._client.delete_session(old_id)

    # A freshened context starts from a clean blackboard too. Artifacts are
    # run-scoped, so the new run is empty by construction; the old run's
    # deliverables are dropped explicitly because `/clear` means "discard
    # that attempt", not "archive it". The trailing `_publish` re-broadcasts
    # `agent.changed`, so the sidebar and in-chat deliverables refetch.
    if prior is not None:
        await manager._clear_artifacts(prior.id)

    run = await manager._open_run(agent_id, trigger=trigger, inherit_session=keep_id)
    if run is not None:
        await manager._patch_run(
            run.id,
            status="idle",
            active_prompt=None,
            result_summary=None,
            error=None,
        )
    await manager._publish(agent_id, agent_run_id=run.id if run else None)


async def compact(manager: AgentManager, agent_id: int, instructions: str = "") -> None:
    """Summarise the agent's conversation to free context (``/compact``).

    Delegates to the SDK's own history compaction: older turns are replaced
    by a summary inside the SDK session, which emits
    ``session.compaction_start``/``_complete`` events the timeline records.
    Only an idle agent can be compacted — the SDK would otherwise rewrite a
    history a turn is still appending to. Raises :class:`ValueError` with a
    user-facing reason.
    """
    agent = await manager._load(agent_id)
    if agent is None:
        raise ValueError("Agent not found.")
    run = await manager._resolve_run(agent_id)
    if run is None or (not run.copilot_session_id and run.id not in manager._live):
        raise ValueError("Nothing to compact yet: this agent has no conversation.")
    if run.status in ("running", "needs_approval"):
        raise ValueError("The agent is busy. Wait for it to finish, or stop it, first.")
    from copilot.generated.rpc import SessionHistoryCompactRequest, Trigger

    live = await manager._ensure_live(agent, run)
    result = await live.sdk_session.rpc.history.compact(
        SessionHistoryCompactRequest(
            custom_instructions=instructions.strip() or None, trigger=Trigger.MANUAL
        ),
        timeout=_COMPACT_TIMEOUT_SECONDS,
    )
    if not result.success:
        raise ValueError("Compaction failed; the conversation was left as it was.")
    await manager._publish(agent_id, agent_run_id=run.id)


async def rerun_task(
    manager: AgentManager, agent_id: int, *, extra: str | None = None, trigger: str = "manual"
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
    await manager.clear_session(agent_id, keep_id=True, trigger=trigger)
    agent = await manager._load(agent_id)
    if agent is None:
        return
    prompt = (agent.task_prompt or "").strip()
    if extra:
        extra = extra.strip()
        prompt = f"{prompt}\n\n{extra}" if prompt else extra
    if not prompt:
        return
    await manager.send_message(agent_id, prompt)


# Registry of system slash commands available inside an agent session:
# name -> async handler. The set of supported names (used for validation and
# the rejection message) is derived from these keys, and the frontend picker
# mirrors it via AGENT_SLASH_COMMANDS, so a new command is a single entry.
COMMAND_HANDLERS: dict[str, Callable[[AgentManager, int, str], Awaitable[None]]] = {
    "rename": cmd_rename,
    "archive": cmd_archive,
    "clear": cmd_clear,
    "compact": cmd_compact,
    "role": cmd_role,
    "memory-store": cmd_memory_store,
    "memory-update": cmd_memory_update,
}
