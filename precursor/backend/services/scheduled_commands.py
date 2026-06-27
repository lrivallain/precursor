"""Run slash commands from scheduled topic prompts.

Scheduled runs execute on the backend (the scheduler), but slash commands
(``/agent``, ``/gh-sync``, …) are otherwise dispatched only in the frontend
composer (``ChatPanel``). So a scheduled prompt of ``/agent test`` used to be
sent verbatim to the LLM as plain chat text.

This module bridges that gap: when a scheduled prompt begins with a recognised
slash command we run the command's backend action headlessly (reusing the same
router/service functions the HTTP endpoints use) and record a receipt in the
transcript, instead of forwarding the literal text to the model. User-defined
skills are expanded the same way the composer expands them, and anything that
isn't a command falls through to a normal LLM turn.

A few commands are inherently interactive (they open a modal or picker in the
UI — ``/notes``, ``/reminder``, a bare ``/role``) and have no meaningful
headless behaviour; those record an explanatory note rather than acting.

Keep ``BUILTIN_TOPIC_COMMANDS`` in sync with the topic surface in
``frontend/src/lib/commands.ts`` (``commandsForSurface("topic")``).
"""

from __future__ import annotations

import logging
import re

import anyio
from fastapi import HTTPException
from sqlalchemy import delete, select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import Message, MessageRole, Role, Topic
from precursor.backend.services.events import (
    publish_message_changed,
    publish_topic_changed,
)
from precursor.backend.services.turn import run_topic_turn

logger = logging.getLogger(__name__)

# Built-in slash commands offered on the topic composer. Mirrors the frontend
# ``commandsForSurface("topic")`` set so the scheduler dispatches exactly the
# commands a user can type in the chat.
BUILTIN_TOPIC_COMMANDS: frozenset[str] = frozenset(
    {
        "gh-update",
        "gh-sync",
        "gh-create",
        "gh-close",
        "notes",
        "rename",
        "new",
        "pin",
        "unpin",
        "reminder",
        "reminder-cancel",
        "done",
        "clear",
        "role",
        "archive",
        "agent",
        "memory-store",
        "memory-list",
        "memory-update",
    }
)

_SLASH_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9-]*)\s*([\s\S]*)$")


def parse_command(prompt: str) -> tuple[str, str] | None:
    """Recognise a leading ``/word …`` slash command.

    Returns ``(name, argument)`` for any slash-prefixed input, or ``None`` for a
    normal prompt. The caller decides whether ``name`` is a known built-in, a
    user skill, or an unknown command to forward verbatim.
    """
    text = prompt.lstrip()
    if not text.startswith("/"):
        return None
    match = _SLASH_RE.match(text)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_scheduled_prompt(topic_id: int, prompt: str, *, clear_context: bool = False) -> None:
    """Execute a scheduled prompt, dispatching slash commands when present.

    * A recognised built-in command runs its backend action and records a
      receipt.
    * A user skill expands to its instructions (LLM turn), persisting the
      literal command as the user turn.
    * Anything else (plain text or an unknown ``/word``) runs a normal turn.
    """
    command = parse_command(prompt)
    if command is None:
        await run_topic_turn(topic_id, prompt, clear_context=clear_context)
        return

    name, argument = command

    if clear_context:
        await _clear_messages(topic_id)

    if name in BUILTIN_TOPIC_COMMANDS:
        await _dispatch_builtin(topic_id, name, argument, literal=prompt.strip())
        return

    skill_instructions = await _load_skill_instructions(name)
    if skill_instructions is not None:
        expanded = f"{skill_instructions.strip()}\n\n---\n\n{argument}".strip()
        # clear_context already handled above; don't wipe twice.
        await run_topic_turn(topic_id, prompt.strip(), llm_prompt=expanded)
        return

    # Unknown command — preserve the old behaviour and forward it to the LLM.
    await run_topic_turn(topic_id, prompt)


async def run_scheduled_prompt_with_timeout(
    topic_id: int, prompt: str, timeout: float, *, clear_context: bool = False
) -> None:
    """Timeout wrapper mirroring ``turn.run_topic_turn_with_timeout``.

    Uses anyio's cancel scope (not ``asyncio.timeout``) so the MCP client task
    groups opened by an LLM turn unwind cleanly; raises ``TimeoutError``, which
    the scheduler already records as an error.
    """
    with anyio.fail_after(timeout):
        await run_scheduled_prompt(topic_id, prompt, clear_context=clear_context)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _dispatch_builtin(topic_id: int, name: str, argument: str, *, literal: str) -> None:
    handler = _BUILTIN_HANDLERS.get(name)
    if handler is None:  # pragma: no cover - guarded by membership check above
        await _record(topic_id, f"`/{name}` is not supported in scheduled runs.")
        return
    try:
        await handler(topic_id, argument)
    except HTTPException as exc:
        # The reused router functions raise HTTPException for user-facing
        # failures (no token, feature disabled, …). Surface the detail in-chat
        # rather than failing the whole schedule.
        await _record(topic_id, f"`/{name}` failed: {exc.detail}")
    except Exception as exc:  # record and keep the schedule healthy
        logger.warning("Scheduled command /%s for topic %s failed: %s", name, topic_id, exc)
        await _record(topic_id, f"`/{name}` failed: {exc}")


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


# A leading "/clear" inside an "/agent <uuid> …" follow-up resets the target
# agent's context before the (optional) prompt is sent. Matched case-insensitively
# so "/Clear" works like the composer's command parsing.
_AGENT_CLEAR_DIRECTIVE_RE = re.compile(r"^/clear\b\s*([\s\S]*)$", re.IGNORECASE)


def _split_clear_directive(follow_up: str) -> tuple[bool, str]:
    """Split a leading ``/clear`` directive off an ``/agent`` follow-up.

    Returns ``(reset, remaining)``: ``reset`` is True when the follow-up began
    with ``/clear`` (the agent's context should be wiped first), and
    ``remaining`` is the prompt to send afterwards (possibly empty).
    """
    match = _AGENT_CLEAR_DIRECTIVE_RE.match(follow_up.strip())
    if match:
        return True, match.group(1).strip()
    return False, follow_up


async def _handle_agent(topic_id: int, argument: str) -> None:
    from precursor.backend.routers.agents import (
        _get_or_404,
        create_agent,
        send_to_agent,
    )
    from precursor.backend.schemas.agent import AgentSendRequest, AgentSessionCreate
    from precursor.backend.services.agents.manager import get_agent_manager

    arg = argument.strip()
    # "/agent <session-id> <prompt>" continues an existing session when the
    # first token resolves to a real agent; otherwise it's a new task.
    ref_match = re.match(
        r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)\b\s*([\s\S]*)$",
        arg,
        re.IGNORECASE,
    )
    async with SessionLocal() as session:
        if ref_match:
            ref, follow_up = ref_match.group(1), ref_match.group(2).strip()
            try:
                existing = await _get_or_404(session, ref)
            except HTTPException:
                existing = None
            if existing is not None:
                # "/agent <uuid> /clear <prompt>" wipes the agent's context
                # *before* sending the prompt, keeping the same uuid so this
                # schedule keeps targeting it. This bounds token growth on a
                # recurring nudge: each run starts from a clean transcript rather
                # than replaying an ever-growing history.
                fresh, follow_up = _split_clear_directive(follow_up)
                if fresh:
                    await get_agent_manager().clear_session(existing.id, keep_id=True)
                if not follow_up:
                    if fresh:
                        await _record(topic_id, f'Cleared the context of agent "{existing.title}".')
                        return
                    await _record(
                        topic_id,
                        f"`/agent`: session `{ref}` already exists; provide a follow-up "
                        "message to send it.",
                    )
                    return
                await send_to_agent(str(existing.id), AgentSendRequest(message=follow_up), session)
                receipt = f'Sent a follow-up to agent "{existing.title}"'
                receipt += " with a fresh context." if fresh else "."
                await _record(topic_id, receipt)
                return
            # Not a real session id — fall through and treat the text as a task.

        if not arg:
            await _record(topic_id, "`/agent` needs a task, e.g. `/agent run the smoke tests`.")
            return

        created = await create_agent(AgentSessionCreate(task=arg, topic_id=topic_id), session)
    await _record(topic_id, f'Started agent "{created.title}".')


async def _handle_gh_sync(topic_id: int, argument: str) -> None:
    from precursor.backend.routers.commands import gh_sync

    async with SessionLocal() as session:
        await gh_sync(topic_id, get_settings(), session)


async def _handle_gh_update(topic_id: int, argument: str) -> None:
    from precursor.backend.routers.commands import (
        CommentPostRequest,
        DraftRequest,
        gh_update_draft,
        gh_update_post,
    )

    async with SessionLocal() as session:
        draft = await gh_update_draft(topic_id, DraftRequest(text=argument or None), session)
        await gh_update_post(
            topic_id, CommentPostRequest(body=draft.draft), get_settings(), session
        )


async def _handle_gh_create(topic_id: int, argument: str) -> None:
    from precursor.backend.routers.commands import (
        DraftRequest,
        GhCreatePostRequest,
        gh_create_draft,
        gh_create_post,
    )

    async with SessionLocal() as session:
        draft = await gh_create_draft(topic_id, DraftRequest(text=argument or None), session)
        await gh_create_post(
            topic_id,
            GhCreatePostRequest(title=draft.title, body=draft.body),
            get_settings(),
            session,
        )


async def _handle_gh_close(topic_id: int, argument: str) -> None:
    from precursor.backend.routers.commands import (
        DraftRequest,
        GhClosePostRequest,
        gh_close_draft,
        gh_close_post,
    )

    async with SessionLocal() as session:
        draft = await gh_close_draft(topic_id, DraftRequest(text=argument or None), session)
        await gh_close_post(topic_id, GhClosePostRequest(body=draft.draft), get_settings(), session)


async def _handle_rename(topic_id: int, argument: str) -> None:
    title = argument.strip()
    if not title:
        await _record(topic_id, "Usage: `/rename <new title>`")
        return
    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return
        topic.title = title[:255]
        await session.commit()
    await publish_topic_changed(topic_id)
    await _record(topic_id, f'Renamed this topic to "{title[:255]}".')


async def _handle_new(topic_id: int, argument: str) -> None:
    from precursor.backend.routers.topics import create_topic
    from precursor.backend.schemas import TopicCreate

    title = argument.strip()
    if not title:
        await _record(topic_id, "Usage: `/new <title>`")
        return
    async with SessionLocal() as session:
        created = await create_topic(TopicCreate(title=title, parent_id=topic_id), session)
    await _record(topic_id, f'Created a nested topic "{created.title}".')


async def _handle_pin(topic_id: int, argument: str) -> None:
    await _set_pinned(topic_id, True)


async def _handle_unpin(topic_id: int, argument: str) -> None:
    await _set_pinned(topic_id, False)


async def _handle_clear(topic_id: int, argument: str) -> None:
    await _clear_messages(topic_id)
    await _record(topic_id, "Cleared this topic's transcript.")


async def _handle_archive(topic_id: int, argument: str) -> None:
    from datetime import UTC, datetime

    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return
        if topic.archived_at is None:
            topic.archived_at = datetime.now(UTC)
            await session.commit()
    await publish_topic_changed(topic_id)


async def _handle_role(topic_id: int, argument: str) -> None:
    name = argument.strip()
    if not name:
        await _record(topic_id, "Usage: `/role <role name>` (the picker isn't available headless).")
        return
    async with SessionLocal() as session:
        role = (
            await session.execute(select(Role).where(Role.name.ilike(name)))
        ).scalar_one_or_none()
        if role is None:
            await _record(topic_id, f'Unknown role "{name}". Manage roles in Settings → Roles.')
            return
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return
        topic.role_id = None if role.is_default else role.id
        await session.commit()
    await publish_topic_changed(topic_id)
    await _record(topic_id, f'Assistant role set to "{role.name}".')


async def _handle_reminder_cancel(topic_id: int, argument: str) -> None:
    from precursor.backend.services.reminders import delete_reminder

    async with SessionLocal() as session:
        existed = await delete_reminder(session, "topic", topic_id)
    if not existed:
        await _record(topic_id, "No reminder to cancel.")


async def _handle_done(topic_id: int, argument: str) -> None:
    from precursor.backend.services.reminders import delete_reminder

    async with SessionLocal() as session:
        existed = await delete_reminder(session, "topic", topic_id)
    if not existed:
        await _record(topic_id, "No reminder to mark done.")


async def _handle_notes(topic_id: int, argument: str) -> None:
    await _record(
        topic_id,
        "`/notes` opens an interactive scratch pad and can't run in a scheduled run.",
    )


async def _handle_reminder(topic_id: int, argument: str) -> None:
    await _record(
        topic_id,
        "`/reminder` needs a date/time picker and can't be set from a scheduled run.",
    )


async def _handle_memory_store(topic_id: int, argument: str) -> None:
    from precursor.backend.services import memories as memory_service

    try:
        payload = memory_service.parse_store_arg(argument)
    except ValueError as exc:
        await _record(topic_id, str(exc))
        return
    async with SessionLocal() as session:
        memory = await memory_service.create_memory(session, payload)
    await _record(topic_id, f"Saved memory #{memory.id} [{memory.kind}].")


async def _handle_memory_update(topic_id: int, argument: str) -> None:
    from precursor.backend.services import memories as memory_service

    try:
        memory_id, payload = memory_service.parse_update_arg(argument)
    except ValueError as exc:
        await _record(topic_id, str(exc))
        return
    async with SessionLocal() as session:
        try:
            memory = await memory_service.update_memory(session, memory_id, payload)
        except LookupError as exc:
            await _record(topic_id, str(exc))
            return
    await _record(topic_id, f"Updated memory #{memory.id} [{memory.kind}].")


async def _handle_memory_list(topic_id: int, argument: str) -> None:
    from precursor.backend.services import memories as memory_service

    async with SessionLocal() as session:
        listing = await memory_service.render_memory_list(session)
    await _record(topic_id, listing)


_BUILTIN_HANDLERS = {
    "agent": _handle_agent,
    "gh-sync": _handle_gh_sync,
    "gh-update": _handle_gh_update,
    "gh-create": _handle_gh_create,
    "gh-close": _handle_gh_close,
    "rename": _handle_rename,
    "new": _handle_new,
    "pin": _handle_pin,
    "unpin": _handle_unpin,
    "clear": _handle_clear,
    "archive": _handle_archive,
    "role": _handle_role,
    "reminder-cancel": _handle_reminder_cancel,
    "done": _handle_done,
    "notes": _handle_notes,
    "reminder": _handle_reminder,
    "memory-store": _handle_memory_store,
    "memory-list": _handle_memory_list,
    "memory-update": _handle_memory_update,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_pinned(topic_id: int, pinned: bool) -> None:
    async with SessionLocal() as session:
        topic = await session.get(Topic, topic_id)
        if topic is None:
            return
        if topic.pinned == pinned:
            await _record(topic_id, "Already pinned." if pinned else "Not pinned.")
            return
        topic.pinned = pinned
        await session.commit()
    await publish_topic_changed(topic_id)
    await _record(topic_id, "Pinned this topic." if pinned else "Unpinned this topic.")


async def _clear_messages(topic_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Message).where(Message.topic_id == topic_id))
        await session.commit()
    await publish_message_changed(topic_id)


async def _record(topic_id: int, content: str) -> None:
    """Append a system message recording a command's outcome, and notify."""
    async with SessionLocal() as session:
        session.add(Message(topic_id=topic_id, role=MessageRole.SYSTEM, content=content))
        await session.commit()
    await publish_message_changed(topic_id)


async def _load_skill_instructions(name: str) -> str | None:
    from precursor.backend.services import skills as skills_service

    async with SessionLocal() as session:
        return await skills_service.get_active_instructions(session, name)
