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

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

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
from precursor.backend.services.github_auth import resolve_github_token
from precursor.backend.services.mcp.client import get_mcp_client_manager
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
# Guard directives
# ---------------------------------------------------------------------------

# A scheduled prompt may begin with one or more "/guard" lines that gate the
# whole run behind a cheap, deterministic MCP probe (no LLM, ~0 tokens). Format:
#
#   /guard <predicate> <server> <tool> [json-args]
#
# <predicate> is "non-empty" (run only when the probe returns items) or "empty"
# (run only when it returns none). The probe calls one MCP tool and classifies
# its result; if the predicate isn't satisfied the run is skipped silently — no
# LLM turn, no chat message — and simply reschedules. This is what stops a poller
# (e.g. an inbox watcher) from burning a full turn every tick just to discover
# there's nothing to do.
#
# A malformed or failing guard "fails open" (the run proceeds) so a typo or a
# transient MCP error can never silently disable a schedule.
_GUARD_LINE_RE = re.compile(r"^/guard\b\s*(.*)$", re.IGNORECASE)
_GUARD_PREDICATES = {"non-empty", "non_empty", "empty"}
# Result texts some tools return in lieu of an empty collection.
_EMPTY_SENTINELS = {
    "",
    "[]",
    "{}",
    "null",
    "none",
    "(empty result)",
    "no results",
    "no emails",
    "no emails to process.",
}


@dataclass(frozen=True)
class _GuardSpec:
    predicate: str  # normalised to "non-empty" or "empty"
    server: str
    tool: str
    args: dict[str, Any]


def _extract_guards(prompt: str) -> tuple[list[str], str]:
    """Peel leading ``/guard …`` lines off a scheduled prompt.

    Returns ``(guard_bodies, remaining_prompt)``. Guards must be the first
    non-blank lines; the first non-guard line ends the block. When no guard line
    is present the prompt is returned unchanged.
    """
    lines = prompt.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    guards: list[str] = []
    while i < len(lines):
        match = _GUARD_LINE_RE.match(lines[i].strip())
        if not match:
            break
        guards.append(match.group(1).strip())
        i += 1
    if not guards:
        return [], prompt
    return guards, "\n".join(lines[i:]).strip()


def _parse_guard(body: str) -> _GuardSpec | None:
    """Parse a guard body ``<predicate> <server> <tool> [json-args]``.

    Returns ``None`` for anything malformed (caller fails open).
    """
    parts = body.split(None, 3)
    if len(parts) < 3:
        return None
    predicate, server, tool = parts[0].lower(), parts[1], parts[2]
    if predicate not in _GUARD_PREDICATES:
        return None
    raw_args = parts[3].strip() if len(parts) == 4 else ""
    if raw_args:
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(args, dict):
            return None
    else:
        args = {}
    predicate = "empty" if predicate == "empty" else "non-empty"
    return _GuardSpec(predicate=predicate, server=server, tool=tool, args=args)


def _coerce_result_value(result: Any) -> Any:
    """Reduce an MCP ``CallToolResult`` to a Python value for emptiness checks."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    texts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
    joined = "\n".join(texts).strip()
    if not joined:
        return None
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        return joined


def _value_is_empty(value: Any) -> bool:
    """Best-effort "no work here" check across common MCP result shapes."""
    if value is None:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_SENTINELS
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):
        # WorkIQ (and other batch-fetch) tools wrap each query in an envelope:
        #   {"results": [{"data": <graph-payload>, "statusCode": 200}, …], …}
        # so the rows live at results[i].data.value, not the top level. Unwrap it
        # first: empty iff every successful payload is empty. A per-result error
        # (statusCode >= 400) is treated as non-empty so a transient failure
        # never looks like "no work" and silently skips the run.
        results = value.get("results")
        if isinstance(results, list):
            return all(_envelope_item_is_empty(item) for item in results)
        # OData/Graph collection (rows under a known key).
        for key in ("value", "items", "messages", "data"):
            inner = value.get(key)
            if isinstance(inner, list):
                return len(inner) == 0
        for key in ("count", "total", "totalCount", "totalItemCount", "@odata.count"):
            inner = value.get(key)
            if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                return inner == 0
        return len(value) == 0
    return False


def _envelope_item_is_empty(item: Any) -> bool:
    """Emptiness of one WorkIQ ``results[]`` entry (``{"data": …, "statusCode": …}``)."""
    if isinstance(item, dict):
        status = item.get("statusCode")
        if isinstance(status, int) and status >= 400:
            return False  # error payload — never count as "no work"
        if "data" in item:
            return _value_is_empty(item["data"])
    return _value_is_empty(item)


async def _probe_guard(spec: _GuardSpec) -> bool | None:
    """Run the guard's MCP tool and report emptiness.

    Returns ``True``/``False`` for empty/non-empty, or ``None`` to *fail open*
    (server unavailable, tool error, or exception) so a broken guard never
    silently disables the schedule.
    """
    manager = get_mcp_client_manager()
    async with SessionLocal() as session:
        github_token = await resolve_github_token(session)
    try:
        async with manager.acquired([spec.server], github_token=github_token) as active:
            if any(srv == spec.server for srv, _ in active.unavailable):
                logger.warning("Guard: MCP server %r unavailable; running anyway", spec.server)
                return None
            result = await active.call_tool(spec.server, spec.tool, spec.args)
    except Exception as exc:
        logger.warning("Guard probe %s/%s failed (%s); running anyway", spec.server, spec.tool, exc)
        return None
    if getattr(result, "isError", False):
        logger.warning(
            "Guard probe %s/%s returned an error result; running anyway", spec.server, spec.tool
        )
        return None
    return _result_is_empty(result)


def _result_is_empty(result: Any) -> bool:
    return _value_is_empty(_coerce_result_value(result))


async def _evaluate_guards(topic_id: int, prompt: str) -> tuple[bool, str]:
    """Evaluate any leading ``/guard`` directives.

    Returns ``(skip, remaining_prompt)``: ``skip`` is True when a guard's
    predicate isn't satisfied (the run should be silently skipped);
    ``remaining_prompt`` is the prompt with guard lines stripped.
    """
    guards, remaining = _extract_guards(prompt)
    for body in guards:
        spec = _parse_guard(body)
        if spec is None:
            logger.warning("Ignoring malformed /guard directive: %r", body)
            continue
        empty = await _probe_guard(spec)
        if empty is None:
            continue  # fail open
        satisfied = empty if spec.predicate == "empty" else not empty
        if not satisfied:
            logger.info(
                "Topic %s: scheduled run skipped by guard (%s %s %s) — no work",
                topic_id,
                spec.predicate,
                spec.server,
                spec.tool,
            )
            return True, remaining
    return False, remaining


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

    A prompt may be prefixed with one or more ``/guard`` directives (see
    :func:`_extract_guards`): a cheap, deterministic MCP probe that gates the
    whole run. When a guard isn't satisfied the run is skipped silently — no LLM
    turn, no chat message — and simply reschedules on the next tick.
    """
    skip, prompt = await _evaluate_guards(topic_id, prompt)
    if skip:
        return

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


# A leading "/clear" or "/run" inside an "/agent <uuid> …" follow-up controls the
# target agent's context before the (optional) prompt is sent. Matched
# case-insensitively so "/Clear" works like the composer's command parsing.
#   /clear [message]  → wipe context, then send [message] as a follow-up
#   /run   [extra]    → wipe context, then replay the agent's own task_prompt
#                       (+ optional [extra] note) — the cheap recurring nudge.
_AGENT_DIRECTIVE_RE = re.compile(r"^/(clear|run)\b\s*([\s\S]*)$", re.IGNORECASE)


def _split_agent_directive(follow_up: str) -> tuple[str | None, str]:
    """Split a leading ``/clear`` or ``/run`` directive off an ``/agent`` follow-up.

    Returns ``(directive, remaining)``: ``directive`` is the lowercased keyword
    (``"clear"`` or ``"run"``) or ``None`` when the follow-up is a plain message;
    ``remaining`` is the text after the directive — a follow-up message for
    ``/clear``, or an extra one-off note for ``/run`` (possibly empty).
    """
    match = _AGENT_DIRECTIVE_RE.match(follow_up.strip())
    if match:
        return match.group(1).lower(), match.group(2).strip()
    return None, follow_up


async def _handle_agent(topic_id: int, argument: str) -> None:
    from precursor.backend.routers.agents import (
        _get_or_404,
        _require_runtime,
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
                # "/clear"/"/run" keep the same uuid so this schedule keeps
                # targeting the agent, while bounding token growth: each run
                # starts from a clean transcript instead of replaying history.
                directive, rest = _split_agent_directive(follow_up)
                if directive == "run":
                    # The cheap recurring nudge: wipe context and replay the
                    # agent's own task_prompt (instructions stored once on the
                    # agent, not re-sent by the schedule every run).
                    await _require_runtime(session)
                    await get_agent_manager().rerun_task(existing.id, extra=rest or None)
                    suffix = " with an extra note" if rest else ""
                    await _record(
                        topic_id,
                        f'Re-ran agent "{existing.title}" from a fresh context{suffix}.',
                    )
                    return
                if directive == "clear":
                    await get_agent_manager().clear_session(existing.id, keep_id=True)
                    if not rest:
                        await _record(topic_id, f'Cleared the context of agent "{existing.title}".')
                        return
                    await send_to_agent(str(existing.id), AgentSendRequest(message=rest), session)
                    await _record(
                        topic_id,
                        f'Sent a follow-up to agent "{existing.title}" with a fresh context.',
                    )
                    return
                if not follow_up:
                    await _record(
                        topic_id,
                        f"`/agent`: session `{ref}` already exists; provide a follow-up "
                        "message to send it.",
                    )
                    return
                await send_to_agent(str(existing.id), AgentSendRequest(message=follow_up), session)
                await _record(topic_id, f'Sent a follow-up to agent "{existing.title}".')
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
