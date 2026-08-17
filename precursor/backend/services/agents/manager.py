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
  agent can read topic context and post results back (``post_message``), plus
  every other catalog MCP server (built-in or user-defined) the user has
  enabled in Settings.
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
import contextvars
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import delete, select

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import (
    AgentEventRecord,
    AgentSession,
    AppSetting,
    Chat,
    Message,
    MessageRole,
    Topic,
)
from precursor.backend.schemas.agent import AgentEvent
from precursor.backend.services.agent_state import build_state_index_prompt
from precursor.backend.services.agents import fleet, runtime
from precursor.backend.services.agents.event_normalizer import normalize_event
from precursor.backend.services.agents.permissions import (
    describe_permission,
    permission_signature,
    should_auto_approve,
)
from precursor.backend.services.app_settings import (
    AGENTS_APPROVAL_POLICIES,
    resolve_agents_approval_policy,
    resolve_agents_context_tier,
    resolve_agents_default_model,
    resolve_agents_enabled,
    resolve_agents_reasoning_effort,
    resolve_agents_system_prompt,
    resolve_agents_watchdog_timeout,
)
from precursor.backend.services.events import (
    publish_agent_changed,
    publish_message_changed,
    publish_message_changed_chat,
    set_current_client_id,
)
from precursor.backend.services.memories import build_memory_prompt
from precursor.backend.services.roles import resolve_role_prompt
from precursor.backend.services.suggestions import (
    SUGGESTIONS_INSTRUCTION,
    split_suggestions,
)
from precursor.backend.services.usage_stats import record_usage

logger = logging.getLogger(__name__)

# An agent reaching one of these has ended its turn, which is what a workflow
# waits on to advance. ``needs_approval`` is included because the coordinator
# uses that pass to surface the approve/deny card on the workflow board, not
# because it moves the run on.
_RESTING_STATUSES = (
    "idle",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "needs_approval",
)

# Slash commands the system intercepts inside an agent session map to real actions
# (rename/clear/archive) handled in ``AgentManager.run_command`` rather than being
# forwarded to the SDK as prompt text. Every *other* slash command is rejected.
_SLASH_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9-]*)\s*([\s\S]*)$")


def parse_agent_command(message: str) -> tuple[str, str] | None:
    """Recognise a leading slash command in a message sent to an agent.

    Returns ``(name, argument)`` for *any* ``/word …`` input (so the caller can
    reject unknown commands instead of leaking them to the SDK), or ``None`` when
    the text is a normal message.
    """
    text = message.lstrip()
    if not text.startswith("/"):
        return None
    match = _SLASH_RE.match(text)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip()


# Cap how long we wait for the out-of-process runtime to come up so a stuck or
# unauthenticated CLI can't block app startup or a settings save indefinitely.
_START_TIMEOUT_SECONDS = 30.0

# How often the watchdog sweeps for stalled running sessions.
_WATCHDOG_INTERVAL_SECONDS = 60.0

# --- Autonomy goal loop --------------------------------------------------------
# When an autonomous agent finishes a turn without declaring completion, we nudge
# it to take the next step toward its objective with this message. It's phrased so
# the model keeps pursuing the durable goal rather than treating it as a new task.
_CONTINUE_NUDGE = (
    "Continue working autonomously toward your objective. Narrate what you're "
    "about to do in one short plain sentence, then take the next concrete step "
    "now. When the objective is fully met, reply with a line "
    "'OBJECTIVE_COMPLETE: <2-3 sentence summary>'. If you are blocked on a "
    "decision only the human can make, reply with 'NEED_INPUT: <your question>'. "
    "Otherwise keep going and report progress several times across the run with "
    "'PROGRESS: <0-100> | <what you just did>'. Publish durable results other "
    "agents may need — one as you finish each phase — with 'ARTIFACT: <title> | "
    "<content>' for a short value, or a multi-line block 'ARTIFACT: <title>' … "
    "'END_ARTIFACT' for a substantial deliverable so its full body is captured."
)

# After this many consecutive no-progress continuation steps, the loop stops and
# parks the agent as ``blocked`` so a human can course-correct instead of letting
# it spin. Kept small — autonomy is about steady progress, not infinite retries.
_STALL_LIMIT = 3

# Appended when an agent has skills switched off. Skills live as files the SDK
# discovers on disk, so there's no kwarg to withhold them — this is a directive,
# not a sandbox. It exists so a focused step ("just translate this") doesn't
# detour through a stored skill that was written for a different context.
_NO_SKILLS_INSTRUCTION = (
    "Do not invoke any stored skill for this task. Solve it directly with your own "
    "reasoning and the material you have been given."
)

# Appended to an autonomous agent's system preamble. It teaches the sentinel
# protocol the goal loop reads back — the agent controls its own lifecycle by
# emitting these lines, so it can run unattended and only pull the human in when
# it genuinely needs a decision.
_AUTONOMY_PROTOCOL = (
    "You are running in AUTONOMOUS mode. Your task above is a durable OBJECTIVE, "
    "not a single question: keep working toward it across multiple turns without "
    "waiting to be prompted each time. After each step you will be nudged to "
    "continue automatically. As you work, narrate what you're doing in one short "
    "plain sentence before each action, so the human can follow along live from "
    "the dashboard.\n\n"
    "Use these control lines to steer your own lifecycle (put each on its own "
    "line, exactly as shown):\n"
    "- 'PROGRESS: <0-100> | <what you just accomplished>' — report several times "
    "across the run (early, middle, and late — not only at the end) so the human "
    "can watch from the dashboard.\n"
    "- 'NEED_INPUT: <question>' — only when you are truly blocked on a decision "
    "or approval that only the human can give. You will pause until they answer.\n"
    "- 'OBJECTIVE_COMPLETE: <2-3 sentence summary>' — when the objective is fully "
    "met. This ends the mission.\n\n"
    "Share durable outputs with the rest of the fleet using ARTIFACT directives "
    "so agents that depend on you receive them as their input; publish one as you "
    "finish each phase or reach a finding.\n"
    "- For a short single-line value: 'ARTIFACT: <title> | <content>'.\n"
    "- For a SUBSTANTIAL or multi-line deliverable (an inventory, a draft, a "
    "review), always use a block so nothing is truncated: put the title on the "
    "ARTIFACT line with NO pipe, then the full Markdown body on the following "
    "lines, then a closing 'END_ARTIFACT' line. For example:\n"
    "    ARTIFACT: Release notes\n"
    "    ## Highlights\n"
    "    - First thing\n"
    "    - Second thing\n"
    "    END_ARTIFACT\n"
    "Put the ENTIRE deliverable inside the artifact (inline body or block) — it "
    "is the real output other agents and the human consume, so never leave it "
    "only in your surrounding prose, and do not append PROGRESS/OBJECTIVE_COMPLETE "
    "onto the artifact body.\n\n"
    "Prefer making progress over asking. Don't ask for confirmation on steps you "
    "can safely take yourself. Stop only when complete or genuinely blocked. The "
    "control lines above are required output: always emit the relevant one even "
    "when base guidance would have you end tersely without a status or recap, "
    "since the system reads them to follow and resurface your mission."
)

# Sentinel directives an autonomous agent embeds in its assistant messages to
# drive its own lifecycle. Parsed only when ``autonomy_enabled`` so a normal
# agent that happens to type these words is unaffected.
#
# Anchored to the *start of a line* (``re.M``) so a directive quoted or explained
# mid-sentence in prose — e.g. an agent narrating "I don't need to emit
# **NEED_INPUT:** to your dashboard" — never misfires and falsely blocks the run.
# ``_DIR_LEAD`` tolerates leading markdown/quote decoration (blockquote, list
# marker, bold/italic, inline code) on the directive line; ``_DIR_POST`` eats the
# closing emphasis of a ``**LABEL:**`` so a stray ``**`` doesn't leak into — and
# unbalance the Markdown of — the captured question/summary.
_DIR_LEAD = r"^[ \t>*_`-]*"
_DIR_POST = r"[ \t*_`]*"
_DIRECTIVE_COMPLETE_RE = re.compile(
    _DIR_LEAD + r"OBJECTIVE[_ ]COMPLETE\s*:" + _DIR_POST + r"(.+)", re.I | re.M
)
_DIRECTIVE_NEED_INPUT_RE = re.compile(
    _DIR_LEAD + r"NEED[_ ]INPUT\s*:" + _DIR_POST + r"(.+)", re.I | re.M
)
_DIRECTIVE_PROGRESS_RE = re.compile(
    _DIR_LEAD + r"PROGRESS\s*:\s*(\d{1,3})\s*(?:\|\s*(.+))?", re.I | re.M
)
# Publish a durable named output to the shared fleet blackboard. Two shapes are
# accepted (see ``_extract_artifacts``): a one-line ``ARTIFACT: <title> | <body>``
# for short values, and a multi-line block that starts with ``ARTIFACT: <title>``
# (no pipe) and runs until an ``END_ARTIFACT`` terminator or the next directive —
# so a substantial deliverable (a list, a draft, a review) is captured whole.
_ARTIFACT_HEADER_RE = re.compile(r"^\s*ARTIFACT\s*:\s*(.*)$", re.I)
_ARTIFACT_END_RE = re.compile(r"^\s*(?:END[_ ]ARTIFACT|/ARTIFACT|ARTIFACT[_ ]END)\s*$", re.I)

# An ordered-list marker (" 1. ", "2. ", …) with a following space, anchored to a
# word boundary so decimals/prices/versions like "3.50" or "v2.0" (no space after
# the dot) are never matched.
_ORDERED_MARKER_RE = re.compile(r"(?:(?<=\s)|^)(\d{1,2})\.\s")


def _split_inline_ordered_list(text: str) -> str:
    """Break a numbered list packed onto one physical line into separate lines.

    A single ``ARTIFACT:`` directive is one line, so a model that writes
    "1. a 2. b 3. c" yields a run-on Markdown paragraph. We split *only* a
    strictly sequential ``1, 2, 3, …`` run so incidental "2." tokens (decimals,
    versions, prices) are left untouched.
    """
    markers = list(_ORDERED_MARKER_RE.finditer(text))
    nums = [int(m.group(1)) for m in markers]
    if len(nums) < 2 or nums != list(range(1, len(nums) + 1)):
        return text
    pieces: list[str] = []
    prev = 0
    for i, m in enumerate(markers):
        if i == 0:
            continue
        pieces.append(text[prev : m.start()].rstrip())
        prev = m.start()
    pieces.append(text[prev:])
    return "\n".join(p for p in pieces if p).strip()


def _normalize_artifact_content(content: str) -> str:
    """Coax single-line ``ARTIFACT:`` content into well-formed Markdown.

    A model can't press Enter inside a one-line directive, so multi-line
    deliverables (lists, paragraphs) collapse. We let it express breaks with an
    escaped ``\\n`` (unescaped here) and, as a safety net, break a packed
    sequential inline numbered list onto its own lines so it renders as a real
    list instead of a run-on line.
    """
    if "\\n" in content or "\\t" in content or "\\r" in content:
        content = (
            content.replace("\\r\\n", "\n")
            .replace("\\r", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
        )
    if "\n" not in content:
        content = _split_inline_ordered_list(content)
    return content


# Control directives never read as "what the agent is doing" — skip them when
# distilling a live narration line so a mission's control channel doesn't leak
# into the dashboard's plain-language activity hint.
_NARRATION_SKIP_RE = re.compile(
    r"^\s*(PROGRESS|NEED[_ ]INPUT|OBJECTIVE[_ ]COMPLETE|ARTIFACT)\s*:", re.I
)


def _clean_narration(text: str) -> str | None:
    """Distil an assistant message into a one-line "what it's doing now" label.

    The Copilot base prompt has the model emit short natural-language *commentary
    preambles* before it acts (e.g. "Let me check the migration script"). Those
    arrive as ordinary assistant text; surfacing the first meaningful line as a
    live narration makes a working agent far more monitorable from the dashboard
    than a bare tool name. We take the first prose line, drop control directives
    and light markdown noise, and cap the length.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _NARRATION_SKIP_RE.match(line):
            continue
        line = re.sub(r"^[#>*\-\s]+", "", line)  # leading heading/list markers
        line = re.sub(r"[*_`]+", "", line).strip()  # inline emphasis/code ticks
        if line:
            return line[:160]
    return None


def _strip_trailing_directives(content: str) -> str:
    """Drop trailing control-directive lines a model glued onto artifact content.

    A model sometimes appends its ``OBJECTIVE_COMPLETE:``/``PROGRESS:`` line to
    the same inline ``ARTIFACT:`` body (often via an escaped ``\\n``), so the
    published artifact ends with a stray control line. We peel those off the tail
    so the stored deliverable is just the deliverable.
    """
    lines = content.split("\n")
    while lines and (not lines[-1].strip() or _NARRATION_SKIP_RE.match(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


# Whole-line control directives (anywhere in the text) plus an ``ARTIFACT`` block
# terminator. Used to scrub a value that will be *shown to the user* as a result,
# so the agent's control channel never leaks into its displayed deliverable.
_CONTROL_LINE_RE = re.compile(
    r"^[ \t>*_`-]*(?:OBJECTIVE[_ ]COMPLETE|NEED[_ ]INPUT|PROGRESS|ARTIFACT|"
    r"END[_ ]ARTIFACT|/ARTIFACT|ARTIFACT[_ ]END)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_control_directives(text: str) -> str:
    """Remove control-directive lines from a value surfaced to the user.

    Directives (``OBJECTIVE_COMPLETE`` / ``NEED_INPUT`` / ``PROGRESS`` /
    ``ARTIFACT`` …) are the agent's control channel, not part of the deliverable.
    We keep the raw assistant message for parsing, forwarding, and gate verdicts,
    but scrub these tokens from anything stored as a displayed *result* so a step's
    output reads as the work itself — not the plumbing that produced it.
    """
    cleaned = _CONTROL_LINE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)  # collapse blank runs the removal left
    return cleaned.strip()


def _extract_artifacts(text: str) -> list[dict[str, str]]:
    """Pull every published artifact from an assistant message.

    Supports two shapes so a substantial deliverable is never truncated:

    * **Inline** — ``ARTIFACT: <title> | <body>`` on one line, for short values.
    * **Block** — a line ``ARTIFACT: <title>`` with no ``|``, then the full
      Markdown body on the following lines, terminated by an ``END_ARTIFACT``
      line, the next control directive, or end of message. This is what lets a
      research inventory, a draft, or a review land whole rather than as a bare
      heading with the real content stranded in prose.
    """
    lines = text.splitlines()
    artifacts: list[dict[str, str]] = []
    i, n = 0, len(lines)
    while i < n:
        header = _ARTIFACT_HEADER_RE.match(lines[i])
        if header is None:
            i += 1
            continue
        rest = header.group(1).strip()
        if "|" in rest:  # inline: 'title | body' on this single line
            title, _, body = rest.partition("|")
            title, body = title.strip(), _normalize_artifact_content(body.strip())
            i += 1
        else:  # block: 'ARTIFACT: title' then body lines until a terminator
            title = rest
            i += 1
            collected: list[str] = []
            while i < n:
                if _ARTIFACT_END_RE.match(lines[i]):
                    i += 1
                    break
                if _NARRATION_SKIP_RE.match(lines[i]):  # next directive ends it
                    break
                collected.append(lines[i])
                i += 1
            body = "\n".join(collected).strip()
        body = _strip_trailing_directives(body)
        if title and body:
            artifacts.append({"title": title[:200], "content": body[:100000]})
    return artifacts


def parse_agent_directives(text: str | None) -> dict[str, Any]:
    """Extract autonomy control directives from an assistant message.

    Returns a dict that may contain ``complete`` (summary str), ``blocked``
    (question str), ``progress`` (``{"value": int, "label": str | None}``),
    and/or ``artifacts`` (``list[{"title": str, "content": str}]``).
    Completion and a raised question are mutually exclusive in effect (completion
    wins), but progress and artifacts can accompany either. Empty dict when
    nothing matched.
    """
    result: dict[str, Any] = {}
    if not text:
        return result
    if (m := _DIRECTIVE_COMPLETE_RE.search(text)) is not None:
        result["complete"] = m.group(1).strip()
    if (m := _DIRECTIVE_NEED_INPUT_RE.search(text)) is not None:
        result["blocked"] = m.group(1).strip()
    if (m := _DIRECTIVE_PROGRESS_RE.search(text)) is not None:
        value = max(0, min(100, int(m.group(1))))
        label = (m.group(2) or "").strip() or None
        result["progress"] = {"value": value, "label": label}
    artifacts = _extract_artifacts(text)
    if artifacts:
        result["artifacts"] = artifacts
    return result


# Long-lived agent SDK sessions bake an OAuth bearer header in at create time
# (the SDK can't refresh a static header). We rebuild the session a little before
# the token actually expires so a transparent re-mint never races a live call.
_OAUTH_REFRESH_MARGIN = timedelta(minutes=5)

# Conservative time-to-live when a token's real expiry can't be determined
# (legacy token saved before we stamped issue time, or no ``expires_in``).
_OAUTH_FALLBACK_TTL = timedelta(minutes=30)

# Sentinel fingerprint for "this session has no MCP servers at all", so that
# switching tools back on rebuilds it rather than reusing a tool-less session.
# Not a valid server name, so it can never collide with a real catalogue.
_MCP_OFF_FINGERPRINT = frozenset({"\x00mcp-off"})


def parse_mcp_scope(raw: str | None) -> frozenset[str] | None:
    """Parse an ``mcp_servers`` CSV into the set of servers a session may see.

    Tri-state, and the empty case is *not* the same as the absent one:

    * ``None`` → ``None``: no scope, attach every enabled server (the behaviour
      before per-step scoping existed).
    * ``"fetch, workiq"`` → ``{"fetch", "workiq"}``: only those.
    * ``""`` (or all-blank) → ``frozenset()``: no servers at all, which the
      caller treats exactly like ``use_mcp=False``.

    Names are never validated against the registry here: a workflow travels
    between machines with different servers installed, and an unknown name
    should simply match nothing rather than fail the run.
    """
    if raw is None:
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


#: The built-in server, attached from :meth:`AgentManager._precursor_mcp_config`
#: rather than from the enabled catalogue.
_PRECURSOR_SERVER = "precursor"


def scope_includes_precursor(scope: frozenset[str] | None) -> bool:
    """Whether a parsed scope lets the first-party ``precursor`` server attach.

    It is exempt from the **Settings → MCP** enabled toggle — it's first-party
    and always available — but not from a step's allowlist: it carries one of
    the larger tool catalogues on a normal install, so a step scoped to one
    server shouldn't pay for topic, memory and schedule schemas it can't need.

    Shared between the attach path and the session fingerprint so the two can't
    disagree about whether it's there; if they did, a step that re-points only
    this server would reuse the wrong catalogue.
    """
    return scope is None or _PRECURSOR_SERVER in scope


# Cap the tool result/error text we archive per event. Tool output (e.g. a
# fetched page) can be huge; the timeline only needs enough to show "what was
# done / why it failed", and the model already got the full payload live.

# Broken-pipe family raised when a JSON-RPC write races the CLI child's stdin
# closing during shutdown. The SDK spawns the Copilot CLI in *our* process group
# (no ``start_new_session``), so on Ctrl+C the child takes the same terminal
# SIGINT and can exit — closing its stdin — before our graceful ``client.stop()``
# finishes its ``runtime.shutdown`` request. The SDK already swallows the write
# error (into a ``StopError`` we suppress), but logs it at WARNING with a full
# traceback first: pure noise on an otherwise-clean shutdown.
_TEARDOWN_PIPE_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    EOFError,
)

# copilot SDK loggers that emit those teardown write failures.
_SDK_PIPE_LOGGERS = ("copilot._jsonrpc", "copilot.client")


class _TeardownPipeNoiseFilter(logging.Filter):
    """Drop copilot SDK records whose exception is an expected teardown pipe error."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc: BaseException | None = record.exc_info[1] if record.exc_info else None
        while exc is not None:
            if isinstance(exc, _TEARDOWN_PIPE_ERRORS):
                return False
            # Walk the chain — the SDK may wrap/chain the underlying pipe error.
            exc = exc.__cause__ or exc.__context__
        return True


@contextlib.contextmanager
def _quiet_sdk_teardown_pipe_noise() -> Iterator[None]:
    """Silence the expected broken-pipe tracebacks the SDK logs while stopping."""
    noise_filter = _TeardownPipeNoiseFilter()
    sdk_loggers = [logging.getLogger(name) for name in _SDK_PIPE_LOGGERS]
    for sdk_logger in sdk_loggers:
        sdk_logger.addFilter(noise_filter)
    try:
        yield
    finally:
        for sdk_logger in sdk_loggers:
            sdk_logger.removeFilter(noise_filter)


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
    # Approval policy resolved once per turn (in ``start_task``/``send_message``)
    # and read by the permission handler. We deliberately do NOT hit the DB from
    # inside the SDK's permission callback — under concurrent writes a transient
    # SQLite lock there would otherwise raise and the SDK turns a raising handler
    # into an opaque, detail-less denial (even in autonomous mode).
    approval_policy: str | None = None
    # The prompt for the turn currently in flight, set when we send a task or a
    # follow-up and cleared once posted to the linked container. Lets us post
    # *every* turn's exchange to the topic/chat (not just the first), keyed to
    # the right prompt rather than always the initial ``task_prompt``.
    pending_prompt: str | None = None
    # Full text of the most recent assistant message for the in-flight turn.
    # ``result_summary`` is capped for the agent list, so we keep the untruncated
    # answer here to repost the complete exchange into the linked topic/chat.
    pending_answer: str | None = None
    # Soonest expiry across any OAuth-protected MCP server attached to this SDK
    # session (today only WorkIQ preview). The bearer header is static, so once
    # this passes we rebuild the session to re-mint it. ``None`` means nothing
    # attached needs refreshing.
    oauth_expires_at: datetime | None = None
    # Set of enabled+registered catalog server names this session was built with
    # (see ``_enabled_catalog_fingerprint``). MCP servers are wired at build time
    # only, so we snapshot the effective set here and rebuild the session when it
    # changes — otherwise a server toggled on in Settings after the session was
    # built stays invisible to the agent until a restart. ``None`` means we didn't
    # attach a catalog (SDK unavailable) and should never rebuild on this basis.
    mcp_fingerprint: frozenset[str] | None = None
    # Enabled OAuth servers this session was built *without*, because no valid
    # credential could be minted for them — each paired with a stamp of the
    # credential as it stood at build time (see ``_auth_skipped_stamps``). The
    # fingerprint above deliberately tracks *enabled* servers, so a signed-out
    # server doesn't read as a change there; without this second signal the
    # tool-less session would be reused forever, even after the user signs back
    # in. Comparing stamps rather than bare names is what keeps it loop-free: a
    # rebuild that still can't attach re-records the same stamps, so nothing
    # fires again until fresh tokens are actually persisted.
    mcp_auth_skipped: frozenset[tuple[str, str]] = frozenset()
    # The (model, reasoning_effort, context_tier) triple currently applied to the
    # live SDK session — set at build time and whenever we ``set_model``. Lets us
    # skip a redundant model switch when the selection hasn't drifted, so every
    # next turn can cheaply reconcile to the current selection.
    model_signature: tuple[str, str | None, str] | None = None
    # --- Autonomy goal-loop state (in-memory, per live session) --------------
    # The last directive block parsed from an assistant message (complete /
    # blocked / progress). Retained for debugging and to avoid double-handling.
    directive: dict[str, Any] | None = None
    # The most recent PROGRESS value the agent self-reported; a fresh value
    # resets the stall counter, a repeated one advances it.
    last_progress: int | None = None
    # Consecutive autonomous steps that produced no measurable progress. When it
    # crosses ``_STALL_LIMIT`` the loop parks the agent as ``blocked`` rather
    # than churning silently.
    stall_count: int = 0


class AgentManager:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._ready = False
        self._live: dict[int, _LiveSession] = {}
        # Durable per-agent timeline. The SDK's ``get_events`` is per-connection
        # (a resumed session only replays ``SessionStartData``), so we archive
        # every streamed event. This in-memory copy is a write-through cache over
        # the ``agent_events`` table: it survives ``teardown_session`` (e.g. on
        # topic link) and, because every event is also persisted, the timeline is
        # reloaded from the DB after a process restart (see ``_ensure_loaded``).
        # Cleared only when the agent is deleted.
        self._events: dict[int, list[AgentEvent]] = {}
        # Agents whose DB archive has been hydrated into ``_events`` this process.
        self._loaded: set[int] = set()
        self._events_lock = asyncio.Lock()
        # Per-agent locks serialising event handling so SDK events are processed
        # in arrival order — otherwise an idle handler can race ahead of the
        # assistant-message handler and post a stale answer back to the topic.
        self._event_locks: dict[int, asyncio.Lock] = {}
        # Per-agent locks serialising session build/resume. ``_ensure_live`` does a
        # check-then-create (read ``_live``, ``create_session``, write ``_live``);
        # without this lock two concurrent callers — e.g. ``start_task`` racing the
        # timeline's ``get_events`` when the detail page is open at startup — both
        # see no cached session and each issue a ``session.create`` for the same
        # ``copilot_session_id``. The duplicate create leaves the CLI's permission
        # responder mis-wired, so every tool call is denied non-interactively
        # ("Permission denied and could not request permission from user").
        self._live_locks: dict[int, asyncio.Lock] = {}
        # Per-agent set of OAuth servers we've already surfaced a sign-in prompt
        # for, so a held session doesn't re-announce ``mcp_auth_required`` on
        # every rebuild/tool error. Cleared once the server attaches with valid
        # creds (so a later token expiry re-announces) or the agent is forgotten.
        self._auth_announced: dict[int, set[str]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task[Any] | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Start the runtime if Agents mode is enabled and usable."""
        async with self._lock:
            if self._ready:
                return
            # Un-stick agents that were mid-turn when the process last died. This
            # runs on every boot BEFORE we try to bring the SDK client up (and
            # regardless of whether it succeeds), because a reload can orphan a
            # ``running`` row with no live task behind it: if the client then
            # fails to start, ``_ready`` stays False, the watchdog never runs,
            # and the row would otherwise stay pinned in ``running`` forever.
            await self._mark_interrupted_on_boot()
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
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        async with self._lock:
            if not self._ready:
                return
            self._ready = False
            # Quiet the SDK's expected broken-pipe tracebacks: on Ctrl+C the CLI
            # child shares our process group and may die before these graceful
            # calls finish writing to it (see ``_quiet_sdk_teardown_pipe_noise``).
            with _quiet_sdk_teardown_pipe_noise():
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
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None

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

    # ------------------------------------------------------------------ watchdog

    async def _watchdog_loop(self) -> None:
        """Periodically interrupt running sessions that have gone silent.

        A turn can wedge (a hung tool, a dropped runtime connection) and leave a
        session pinned in ``running`` forever, never notifying back. This sweep
        flips such sessions to ``interrupted`` (resumable) with a reason, so they
        surface in the UI and the user can Resume to retry the in-flight prompt.
        """
        while self._ready:
            try:
                await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
                await self._watchdog_sweep()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("agent watchdog sweep failed", exc_info=True)

    async def _watchdog_sweep(self) -> None:
        async with SessionLocal() as session:
            timeout = await resolve_agents_watchdog_timeout(session)
            cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
            rows = (
                (
                    await session.execute(
                        select(AgentSession).where(AgentSession.status == "running")
                    )
                )
                .scalars()
                .all()
            )
            stale: list[tuple[int, int | None, int | None]] = []
            reason = (
                f"No runtime activity for over {max(1, timeout // 60)} min — "
                "interrupted by the watchdog. Resume to retry."
            )
            for agent in rows:
                ref = agent.last_activity_at or agent.updated_at or agent.created_at
                if ref is None:
                    continue
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=UTC)
                if ref < cutoff:
                    agent.status = "interrupted"
                    agent.error = reason
                    stale.append((agent.id, agent.topic_id, agent.chat_id))
            if stale:
                await session.commit()
        # Drop any wedged live session so a Resume rebuilds it clean, then signal
        # the UI. Done outside the DB transaction to keep the commit tight.
        for agent_id, topic_id, chat_id in stale:
            logger.warning("agent %s: interrupted by watchdog (idle > %ss)", agent_id, timeout)
            with contextlib.suppress(Exception):
                await self.teardown_session(agent_id)
            await publish_agent_changed(
                agent_session_id=agent_id, topic_id=topic_id, chat_id=chat_id
            )

    # ------------------------------------------------------------------ helpers

    def _spawn(self, coro: Any) -> None:
        # Agent work runs asynchronously in the background, but ``create_task``
        # copies the caller's context — which carries the originating request's
        # ``X-Client-Id`` (set by middleware). Every event this task publishes
        # (live progress *and* the notify-back that marks a linked topic/chat
        # unread) would then be stamped with that id and echo-suppressed in the
        # very tab that started the agent, while other tabs see it. The agent's
        # results aren't "live-streamed" back to the originating tab the way a
        # chat turn is, so clear the client id for the task: its events broadcast
        # to *every* subscriber, including the originator.
        ctx = contextvars.copy_context()
        ctx.run(set_current_client_id, None)
        task = asyncio.create_task(coro, context=ctx)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def enqueue(self, coro: Any) -> None:
        """Schedule a manager coroutine as a tracked background task."""
        self._spawn(coro)

    def _require_ready(self) -> None:
        if not self._ready or self._client is None:
            _ok, detail = runtime.agents_available()
            raise RuntimeError(f"Agents runtime not available: {detail}")

    def _precursor_mcp_config(self, agent: AgentSession) -> dict[str, Any] | None:
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
        # Identity, so the ``state_*`` tools can default to *this* agent's
        # scratchpad. Without it the agent would have to discover its own row id
        # before it could save a cursor for its next run.
        env["PRECURSOR_AGENT_ID"] = str(agent.id)
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

    @staticmethod
    def _entry_to_sdk_config(sdk: Any, entry: Any, github_token: str) -> Any:
        """Translate one ``MCPServerEntry`` into an SDK MCP server config.

        Raises ``ValueError`` for entries the SDK can't represent (missing
        command/url, unknown transport) so the caller can skip + log them.
        """
        if entry.transport == "stdio":
            if not entry.command:
                raise ValueError("stdio server has no command")
            return sdk.MCPStdioServerConfig(
                type="stdio",
                command=entry.command,
                args=list(entry.args),
                # Built-ins set their own env (or None → inherit ours so PATH and
                # the venv resolve); user entries always inherit ours.
                env=entry.env if entry.env is not None else dict(os.environ),
                tools=["*"],
            )
        if entry.transport == "streamable_http":
            if not entry.url:
                raise ValueError("streamable_http server has no url")
            # headers_provider folds in per-request secrets — the GitHub bearer
            # token for the built-in 'github' server, or a user entry's stored
            # headers. Resolved here, never persisted in agent events.
            headers = entry.headers_provider(github_token) if entry.headers_provider else None
            return sdk.MCPHTTPServerConfig(
                type="http",
                url=entry.url,
                headers=headers or None,
                tools=["*"],
            )
        raise ValueError(f"unsupported transport {entry.transport!r}")

    async def _enabled_catalog_fingerprint(
        self, scope: frozenset[str] | None = None
    ) -> frozenset[str]:
        """Names of catalog MCP servers currently enabled *and* registered.

        Excludes ``precursor`` (always attached with full access). Computed the
        same way on both sides of the comparison in :meth:`_ensure_live`, so it
        deliberately reflects the user's toggles rather than which servers
        actually attached — an OAuth server skipped for missing credentials must
        not read as a change and trigger an endless rebuild loop.

        ``scope`` narrows it to a per-agent allowlist (see :func:`parse_mcp_scope`)
        so that re-pointing a shared agent at a differently-scoped workflow step
        reads as a change and rebuilds the session.
        """
        from precursor.backend.services.app_settings import resolve_mcp_enabled
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        async with SessionLocal() as session:
            enabled = await resolve_mcp_enabled(session)
        registered = {entry.name for entry in get_mcp_client_manager().list_entries()}
        return frozenset(
            name
            for name, on in enabled.items()
            if on
            and name != "precursor"
            and name in registered
            and (scope is None or name in scope)
        )

    async def _expected_mcp_fingerprint(self, agent: AgentSession) -> frozenset[str]:
        """The fingerprint a session created for ``agent`` right now would carry.

        Comparing a live session against *this* — rather than against the raw
        enabled set — is what makes both halves of the tool configuration
        rebuild-sensitive: flipping ``use_mcp``, and narrowing or widening the
        per-step server scope. It also keeps a tools-off agent stable, which the
        raw comparison did not: its stored fingerprint is the off sentinel, so it
        never matched the enabled set and every dispatch tore down and rebuilt a
        session that was already correct.

        ``precursor`` is folded in here rather than in
        :meth:`_enabled_catalog_fingerprint`, which answers a narrower question
        (what the user's toggles enable). The first-party server ignores those
        toggles but *is* scopable, so a step that only re-points precursor still
        has to read as a change.
        """
        scope = parse_mcp_scope(agent.mcp_servers)
        if not agent.use_mcp or (scope is not None and not scope):
            return _MCP_OFF_FINGERPRINT
        catalog = await self._enabled_catalog_fingerprint(scope)
        if scope_includes_precursor(scope):
            return catalog | {_PRECURSOR_SERVER}
        return catalog

    async def _catalog_mcp_configs(
        self,
        scope: frozenset[str] | None = None,
    ) -> tuple[dict[str, Any], datetime | None, list[str]]:
        """SDK configs for every catalog MCP server the user has *enabled*.

        Mirrors the chat/topics surface: both built-in servers (``github``,
        ``fetch``, ``workspace-fs``, …) and user-defined ones are attached when
        their ``mcp_enabled`` toggle is on, so an agent can call the same tools.
        ``precursor`` is excluded here — it's attached separately with full
        access in :meth:`_precursor_mcp_config`.

        ``scope``, when given, narrows that to an allowlist of server names (a
        workflow step's ``mcp_servers``); ``None`` attaches everything enabled.

        Returns ``(configs, oauth_expires_at, auth_required)``: ``oauth_expires_at``
        is the soonest expiry across any OAuth-protected server whose bearer token
        we baked into a static header (so the caller can refresh before it lapses,
        ``None`` when nothing attached needs it); ``auth_required`` lists enabled
        OAuth servers we *skipped* because no valid credentials are available, so
        the caller can surface an interactive sign-in prompt instead of leaving
        the agent to discover the tools are silently missing. Returns
        ``({}, None, [])`` if the SDK isn't loadable.
        """
        try:
            sdk = runtime.load_sdk()
        except RuntimeError:
            return {}, None, []

        # Imported lazily to keep this module importable without the MCP service
        # graph in the import path of the agents-unavailable case.
        from precursor.backend.services.app_settings import resolve_mcp_enabled
        from precursor.backend.services.github_auth import resolve_github_token
        from precursor.backend.services.mcp.client import get_mcp_client_manager

        async with SessionLocal() as session:
            enabled = await resolve_mcp_enabled(session)
            github_token = await resolve_github_token(session)

        manager = get_mcp_client_manager()
        configs: dict[str, Any] = {}
        oauth_expires_at: datetime | None = None
        auth_required: list[str] = []
        for entry in manager.list_entries():
            # 'precursor' is first-party and attached with full access elsewhere;
            # never gate or duplicate it here.
            if entry.name == "precursor":
                continue
            # Out of the caller's allowlist. Filtered *before* the credential
            # check below so a step scoped away from an OAuth server doesn't
            # raise a sign-in prompt for tools it was never going to use.
            if scope is not None and entry.name not in scope:
                continue
            if not enabled.get(entry.name, False):
                continue
            try:
                config = self._entry_to_sdk_config(sdk, entry, github_token)
            except ValueError as exc:
                logger.warning("Skipping MCP server '%s': %s", entry.name, exc)
                continue
            # OAuth-protected catalog servers (the hosted WorkIQ preview and the
            # Agent 365 pair) authenticate via an httpx.Auth provider that the
            # SDK's static-header HTTP config can't carry. Mint a concrete bearer
            # token and inject it, or skip the server entirely when sign-in is
            # required — attaching it without credentials would just surface 401s
            # as missing tools to the agent.
            if entry.transport == "streamable_http" and entry.auth_provider is not None:
                bearer = await self._oauth_bearer_header(entry.name)
                if bearer is None:
                    logger.warning(
                        "Skipping MCP server '%s' for agent: no valid credentials "
                        "(surfacing an in-app sign-in prompt)",
                        entry.name,
                    )
                    auth_required.append(entry.name)
                    continue
                header, expires_at = bearer
                # Unknown lifetime → assume a conservative TTL so we still rebuild
                # the session periodically rather than letting a stale header rot.
                if expires_at is None:
                    expires_at = datetime.now(UTC) + _OAUTH_FALLBACK_TTL
                oauth_expires_at = (
                    expires_at if oauth_expires_at is None else min(oauth_expires_at, expires_at)
                )
                existing = dict(config.get("headers") or {})
                existing.update(header)
                config["headers"] = existing
            configs[entry.name] = config
        return configs, oauth_expires_at, auth_required

    @staticmethod
    async def _oauth_bearer_header(name: str) -> tuple[dict[str, str], datetime | None] | None:
        """Resolve a static ``Authorization`` header for an OAuth catalog server.

        Works for every server Precursor can sign in to — the hosted WorkIQ
        preview *and* the Agent 365 pair — by resolving the server's credential
        profile and minting a bearer from it. Returns ``None`` when the server
        has no usable credential (not an OAuth server, preview mode off, no
        tenant resolved, or no valid token) so the caller skips attaching it
        rather than handing the agent an unauthenticated endpoint. On success
        returns ``(header, expires_at)`` where ``expires_at`` may be ``None`` if
        the token's lifetime can't be determined.
        """
        from precursor.backend.services.mcp.oauth_registry import profile_for_server
        from precursor.backend.services.mcp.workiq_preview import resolve_workiq_bearer_token

        profile = await profile_for_server(name)
        if profile is None:
            return None
        resolved = await resolve_workiq_bearer_token(profile)
        if resolved is None:
            return None
        token, expires_at = resolved
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}, expires_at

    @staticmethod
    async def _auth_skipped_stamps(servers: list[str]) -> frozenset[tuple[str, str]]:
        """Stamp each of ``servers`` with the credential it would sign in with.

        Answers "has anything changed about the sign-in for the servers we had to
        skip?" cheaply enough to run on every dispatch: it reads the stored
        credential rows straight from the DB and does no network I/O, no bearer
        minting and no profile/tenant resolution — unlike
        :meth:`_oauth_bearer_header`, which drives a real token refresh.

        The stamp is a digest of the stored credential (empty string when the row
        is absent), never the credential itself, so no token material is retained
        in the manager's memory. Servers sharing one credential — the Agent 365
        pair — naturally stamp identically, since
        :func:`~precursor.backend.services.mcp.oauth_registry.credential_key`
        resolves both to the same row.
        """
        if not servers:
            return frozenset()

        from precursor.backend.services.mcp.oauth_registry import credential_key

        keys = {credential_key(name) for name in servers}
        async with SessionLocal() as session:
            rows = (
                (await session.execute(select(AppSetting).where(AppSetting.key.in_(keys))))
                .scalars()
                .all()
            )
        values = {row.key: row.value or "" for row in rows}
        return frozenset(
            (name, hashlib.sha256(values.get(credential_key(name), "").encode()).hexdigest())
            for name in servers
        )

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

    async def _system_preamble(self, agent: AgentSession) -> str | None:
        """Combined system-message append: role persona + operator custom prompt + memory + topic binding.

        The SDK base prompt isn't ours to set, so each piece is *appended*. The
        agent's Assistant Role persona comes first (it defines who the agent is),
        then the custom prompt (Settings → Agents) as general guidance, long-term
        memory as standing context (matching chat/topic turns), then the topic
        binding so the agent always knows which record it's on.
        """
        async with SessionLocal() as session:
            role_prompt = (await resolve_role_prompt(session, agent.role_id)).strip()
            custom = (await resolve_agents_system_prompt(session)).strip()
            # Long-term memory is standing context, not always wanted: a pure
            # transform step ("translate this") is better off not consulting it.
            memory = await build_memory_prompt(session) if agent.use_memory else ""
            # The agent's own scratchpad from previous runs. Only the *key index*
            # goes in the prompt — the bodies stay in the DB until the agent asks
            # for one with ``state_get``, so a large saved cursor costs nothing
            # per turn. Tool-less agents can't call ``state_get``, so telling them
            # what they can't read would just burn context.
            state = (
                (await build_state_index_prompt(session, agent.id) or "") if agent.use_mcp else ""
            )
        persona = (
            f"Active assistant role — adopt this persona for the whole task:\n{role_prompt}"
            if role_prompt
            else ""
        )
        topic = await self._topic_context(agent)
        autonomy = _AUTONOMY_PROTOCOL if agent.autonomy_enabled else ""
        # Follow-up "suggest" chips are for a human replying turn-by-turn. An
        # autonomous agent drives itself via the control directives and runs
        # unattended, so inviting user-facing follow-ups there just burns tokens
        # and pulls against the "keep going, don't ask" autonomy contract (and the
        # base prompt's "don't offer to continue" tone rule). Only plain agents,
        # which the user converses with, get the suggestions instruction.
        suggestions = "" if agent.autonomy_enabled else SUGGESTIONS_INSTRUCTION
        # Skills are files the SDK discovers on disk, so this is a *directive*
        # rather than a hard sandbox — it tells the agent to solve the task
        # directly instead of reaching for a stored skill.
        skills = "" if agent.use_skills else _NO_SKILLS_INSTRUCTION
        parts = [
            p for p in (persona, custom, memory, state, topic, autonomy, skills, suggestions) if p
        ]
        return "\n\n".join(parts) if parts else None

    # ------------------------------------------------------------------ sessions

    async def _ensure_live(self, agent: AgentSession) -> _LiveSession:
        """Return the live SDK session for ``agent``, creating/resuming it.

        A cached session is reused unless its baked-in OAuth bearer header is
        about to expire (see :meth:`_oauth_stale`): the SDK can't refresh a static
        header, so we transparently tear the session down and recreate it, which
        re-mints the token while resuming the same conversation via
        ``copilot_session_id``. We never refresh mid-turn — only when the agent is
        idle, so an in-flight run is left untouched until its next dispatch.

        The whole check-then-create runs under a per-agent lock so concurrent
        callers (e.g. ``start_task`` racing the timeline's ``get_events`` when the
        agent detail page is open at startup) can't each fire a ``session.create``
        for the same ``copilot_session_id`` — a duplicate create leaves the CLI's
        permission responder mis-wired and every tool call is then denied.
        """
        self._require_ready()
        lock = self._live_locks.setdefault(agent.id, asyncio.Lock())
        async with lock:
            return await self._ensure_live_locked(agent)

    async def _ensure_live_locked(self, agent: AgentSession) -> _LiveSession:
        live = self._live.get(agent.id)
        if live is not None:
            oauth_stale = self._oauth_stale(live)
            catalog_changed = (
                live.mcp_fingerprint is not None
                and live.mcp_fingerprint != await self._expected_mcp_fingerprint(agent)
            )
            # A server we had to skip for missing credentials doesn't move the
            # fingerprint (which tracks *enabled* servers), so without this the
            # tool-less session would be reused forever. Comparing credential
            # stamps rather than names means a rebuild that still can't attach
            # re-records the same stamps and doesn't fire again.
            auth_recovered = live.mcp_auth_skipped != await self._auth_skipped_stamps(
                [name for name, _ in live.mcp_auth_skipped]
            )
            if not oauth_stale and not catalog_changed and not auth_recovered:
                return live
            if agent.status in {"running", "needs_approval", "pending"}:
                # A turn is in flight — don't disrupt it; refresh on the next
                # idle dispatch instead.
                return live
            if oauth_stale:
                reason = "refresh an expiring OAuth token"
            elif catalog_changed:
                reason = "pick up a changed MCP server set"
            else:
                reason = "pick up a recovered MCP sign-in"
            logger.info("Rebuilding agent %s session to %s", agent.id, reason)
            await self.teardown_session(agent.id, forget=False)

        assert self._client is not None
        kwargs: dict[str, Any] = {
            "on_permission_request": self._make_permission_handler(agent.id),
        }
        # Reasoning effort + context tier are global agent prefs (Settings →
        # Agents / composer toolbar). Applied at session creation, mirroring how
        # the model is chosen — a change takes effect on the next new/rebuilt
        # session. The frontend only offers efforts the chosen model supports.
        # The model comes from the DB-resolved selection (what the composer
        # writes), not the env/config constant, so a new agent honours the
        # currently selected model instead of the factory default. An explicit
        # per-agent pin (``agent.model``) still wins.
        async with SessionLocal() as s:
            default_model = await resolve_agents_default_model(s)
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
        model = agent.model or default_model
        model = await self._sanitize_model(agent.id, model)
        if model:
            kwargs["model"] = model
        if effort:
            kwargs["reasoning_effort"] = effort
        if tier and tier != "default":
            kwargs["context_tier"] = tier
        if agent.copilot_session_id:
            kwargs["session_id"] = agent.copilot_session_id
        # An agent with MCP switched off gets no tool servers at all — not even
        # the first-party one. Whole catalogues of tool schemas are a large,
        # fixed context cost, so a step that only has to transform text pays
        # nothing for tools it will never call. An explicitly *empty* server
        # scope says the same thing in the other vocabulary, and means it.
        scope = parse_mcp_scope(agent.mcp_servers)
        scoped_to_nothing = scope is not None and not scope
        tools_on = agent.use_mcp and not scoped_to_nothing
        oauth_expires_at: datetime | None = None
        auth_required: list[str] = []
        if tools_on:
            mcp: dict[str, Any] = {}
            # ``precursor`` ignores the mcp_enabled toggle (it's first-party and
            # always available) but is not exempt from the scope — see
            # scope_includes_precursor. Attached here rather than via
            # _catalog_mcp_configs so it keeps its full-access env.
            if scope_includes_precursor(scope):
                mcp.update(self._precursor_mcp_config(agent) or {})
            # Every enabled catalog server the scope allows (built-in +
            # user-defined). _catalog_mcp_configs already skips 'precursor', so
            # the first-party full-access entry above can't be shadowed.
            catalog, oauth_expires_at, auth_required = await self._catalog_mcp_configs(scope)
            mcp.update(catalog)
            if mcp:
                kwargs["mcp_servers"] = mcp
        # Snapshot what this session carries so a later toggle — or a step with a
        # different scope reusing this agent — rebuilds it. Computed by the same
        # method the reuse check compares against, so the two can't drift.
        mcp_fingerprint = await self._expected_mcp_fingerprint(agent)
        preamble = await self._system_preamble(agent)
        if preamble:
            # Append (don't replace) so the agent keeps its SDK base instructions
            # but also gets the operator's custom guidance and any topic binding.
            kwargs["system_message"] = {"mode": "append", "content": preamble}

        sdk_session = await self._client.create_session(**kwargs)
        live = _LiveSession(
            sdk_session=sdk_session,
            oauth_expires_at=oauth_expires_at,
            mcp_fingerprint=mcp_fingerprint,
            mcp_auth_skipped=await self._auth_skipped_stamps(auth_required),
            model_signature=(model, effort or None, tier or "default") if model else None,
        )
        self._live[agent.id] = live

        # Wire the event stream. The SDK invokes this synchronously; defer the
        # async work (DB + bus) onto the loop.
        sdk_session.on(lambda event: self._spawn(self._handle_event(agent.id, event)))

        # Persist the resume handle the first time round.
        sid = getattr(sdk_session, "id", None) or getattr(sdk_session, "session_id", None)
        if sid and not agent.copilot_session_id:
            await self._patch(agent.id, copilot_session_id=str(sid))

        # Any OAuth server we couldn't attach for lack of credentials is surfaced
        # as an in-app sign-in prompt (drives the global McpAuthBanner) instead of
        # leaving the agent to hit "tool not available" and improvise an answer.
        await self._announce_auth_required(agent.id, auth_required)
        return live

    @staticmethod
    def _oauth_stale(live: _LiveSession) -> bool:
        """True when ``live``'s baked-in OAuth token is at/within the refresh margin."""
        expires_at = live.oauth_expires_at
        if expires_at is None:
            return False
        return datetime.now(UTC) >= expires_at - _OAUTH_REFRESH_MARGIN

    async def _auth_server_from_failed_tool(self, event: AgentEvent) -> str | None:
        """Return the OAuth server to prompt for when a tool failure looks like
        an expired sign-in, else ``None``.

        We require the event to name a server Precursor can actually sign in to
        *and* the bearer to be genuinely unavailable, so a routine tool error
        (bad args, server-side fault) never nags the user to re-auth. Servers
        that can't sign in as things stand resolve to no profile and are
        ignored — notably ``workiq`` with preview mode off, which runs as local
        stdio with no OAuth, so a routine stdio tool error must not surface a
        prompt the user can't act on (re-auth 400s with "Enable WorkIQ preview
        mode before signing in").
        """
        if event.tool_status != "error":
            return None
        server = (event.data or {}).get("server_name")
        if not isinstance(server, str) or not server:
            return None
        from precursor.backend.services.mcp.oauth_registry import profile_for_server
        from precursor.backend.services.mcp.workiq_preview import resolve_workiq_bearer_token

        profile = await profile_for_server(server)
        if profile is None:
            return None
        if await resolve_workiq_bearer_token(profile) is not None:
            return None
        return server

    async def _emit_synthetic(self, agent_id: int, event: AgentEvent) -> None:
        """Append a manager-originated event to the timeline (archive + publish).

        Used for events the SDK never sends — currently ``mcp_auth_required`` —
        so they persist in the durable timeline and reach the frontend over the
        same ``agent.changed`` bus as real SDK events.
        """
        await self._ensure_loaded(agent_id)
        event.at = datetime.now(UTC)
        self._events.setdefault(agent_id, []).append(event)
        await self._archive_event(agent_id, event)
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )

    async def _announce_auth_required(self, agent_id: int, servers: list[str]) -> None:
        """Surface a sign-in prompt for each ``server`` we couldn't authenticate.

        Collapsed per credential first: the Agent 365 servers share one Entra
        token, so announcing both would raise two prompts the user can only
        answer once. De-duped per agent on top of that, so a held session
        doesn't re-announce on every rebuild. Servers that are *not* currently
        blocked are dropped from the announced set, so a later token expiry (or
        a sign-in that's since lapsed) prompts again rather than staying silent.
        """
        from precursor.backend.services.mcp.oauth_registry import (
            collapse_by_credential,
            server_label,
        )

        pending = collapse_by_credential(servers)
        announced = self._auth_announced.setdefault(agent_id, set())
        for server in pending:
            if server in announced:
                continue
            announced.add(server)
            label = server_label(server)
            await self._emit_synthetic(
                agent_id,
                AgentEvent(
                    kind="mcp_auth_required",
                    tool_name=server,
                    text=f"{label} needs you to sign in to use its tools.",
                    data={"server": server},
                ),
            )
        # Reset servers that authenticated this build so a future lapse re-fires.
        announced.intersection_update(pending)

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
        if not self.ready:
            return
        for agent_id in list(self._live):
            agent = await self._load(agent_id)
            if agent is not None and agent.status in {"running", "needs_approval", "pending"}:
                continue
            await self.teardown_session(agent_id, forget=False)
            self._auth_announced.pop(agent_id, None)

    async def _release_parked_turn(self, agent_id: int, live: Any) -> None:
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
                fut.set_result(self._reject("superseded by a new run"))
        with contextlib.suppress(Exception):
            await live.sdk_session.abort()

    async def start_task(self, agent_id: int, extra_context: str | None = None) -> None:
        try:
            agent = await self._load(agent_id)
            if agent is None:
                return
            # A fresh objective run starts from a clean blackboard: drop any
            # artifacts a previous run published so the new turn's deliverables
            # replace them rather than piling up beside stale ones. A no-op on a
            # first run (nothing published yet).
            await self._clear_artifacts(agent_id)
            live = await self._ensure_live(agent)
            # A step that named a server it couldn't sign in to must not run: the
            # agent would answer from its own knowledge and the workflow would
            # record that as a success. Park it so the run pauses on the sign-in.
            blocked_on = self._blocked_on_missing_auth(agent, live)
            if blocked_on:
                await self._block_turn(agent_id, blocked_on)
                return
            await self._release_parked_turn(agent_id, live)
            await self._sync_selected_model(agent)
            live.approval_policy = await self._approval_policy(agent)
            prompt = (agent.task_prompt or "").strip() or None
            live.pending_prompt = prompt
            live.pending_answer = None
            # Fresh human intent → reset the autonomy budget and stall tracking so
            # the objective run starts from a clean step count.
            live.stall_count = 0
            live.last_progress = None
            await self._patch(
                agent_id,
                status="running",
                error=None,
                active_prompt=prompt,
                step_count=0,
                blocked_question=None,
            )
            await publish_agent_changed(
                agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
            )
            # Fleet dependents receive their upstreams' published artifacts as a
            # kickoff preamble ahead of the durable objective, so results flow
            # down the DAG without a human relaying them.
            sent = agent.task_prompt
            if extra_context:
                sent = f"{extra_context}\n\n---\n\n{agent.task_prompt}"
            await live.sdk_session.send(sent)
        except Exception as exc:
            await self._fail_turn(agent_id, exc)

    async def restart_with_task(self, agent_id: int) -> None:
        """Re-establish the SDK session after the task prompt was edited.

        The task is delivered only by :meth:`start_task`; a live or resumed
        session keeps the *previous* instructions in its history, so an edited
        ``task_prompt`` stays inert until it is replayed. Drop the in-memory
        session (``forget=False`` keeps the visible timeline) so the next connect
        refreshes the system preamble, then replay the new task.

        ``copilot_session_id`` is deliberately left untouched: scheduled
        ``/agent <uuid>`` nudges target that id, and recreating an agent (which
        mints a new id) is exactly what silently breaks such schedules. Callers
        that want a clean-slate context use the ``clear`` command instead.
        """
        await self.teardown_session(agent_id)
        await self.start_task(agent_id)

    async def send_message(self, agent_id: int, text: str) -> None:
        try:
            agent = await self._load(agent_id)
            if agent is None:
                return
            live = await self._ensure_live(agent)
            await self._sync_selected_model(agent)
            live.approval_policy = await self._approval_policy(agent)
            prompt = text.strip() or None
            live.pending_prompt = prompt
            live.pending_answer = None
            # A human message is fresh intent: reset the autonomy budget and, if
            # the agent had parked itself with a question, clear that block — this
            # message is the answer that unsticks it.
            live.stall_count = 0
            live.last_progress = None
            await self._patch(
                agent_id,
                status="running",
                active_prompt=prompt,
                step_count=0,
                blocked_question=None,
            )
            await publish_agent_changed(
                agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
            )
            await live.sdk_session.send(text)
        except Exception as exc:
            await self._fail_turn(agent_id, exc)

    async def resume(self, agent_id: int) -> None:
        """Re-run the in-flight turn of an interrupted session.

        Re-sends the persisted ``active_prompt`` (the turn cut off by a restart
        or the watchdog) so it finishes and notifies back. A no-op when there's
        nothing tracked to resume.
        """
        agent = await self._load(agent_id)
        if agent is None:
            return
        prompt = (agent.active_prompt or "").strip()
        if not prompt:
            return
        try:
            live = await self._ensure_live(agent)
            await self._sync_selected_model(agent)
            live.approval_policy = await self._approval_policy(agent)
            live.pending_prompt = prompt
            live.pending_answer = None
            await self._patch(agent_id, status="running", error=None)
            await publish_agent_changed(
                agent_session_id=agent_id, topic_id=agent.topic_id, chat_id=agent.chat_id
            )
            await live.sdk_session.send(prompt)
        except Exception as exc:
            await self._fail_turn(agent_id, exc)

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
        fut.set_result(self._decision(decision))
        # The turn resumes, so the agent is working again. This *must* happen
        # here rather than at each call site: ``needs_approval`` is a sticky
        # status the idle handler deliberately skips (so a trailing idle can't
        # mask a genuinely parked agent), which means an agent left sitting in it
        # never reaches ``_on_idle`` — its turn finishes, the workflow is never
        # told, and the step stays "Running" forever.
        await self._patch(agent_id, status="running", blocked_question=None)
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )
        return True

    def list_permissions(self) -> list[dict[str, Any]]:
        """Recap of active "approve for session" grants across live sessions."""
        out: list[dict[str, Any]] = []
        for agent_id, live in self._live.items():
            for grant in live.grants:
                out.append({"agent_id": agent_id, **grant})
        out.sort(key=lambda g: g.get("at") or datetime.min.replace(tzinfo=UTC), reverse=True)
        return out

    def live_activity(self, agent_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Snapshot each agent's in-flight work for the dashboard cockpit.

        Reads the in-memory event cache (no DB, no ``await``) to derive, per
        agent: the currently running tool, how many tool calls are running in
        parallel (the "sub-agent fan-out" cluster), a plain-language **narration**
        line distilled from the agent's own commentary this turn, and the oldest
        unresolved permission request. Everything resets at turn boundaries so a
        dropped completion event can't leave a tool "running" across turns.
        Agents not live in-process report an empty snapshot.
        """
        out: dict[int, dict[str, Any]] = {}
        for aid in agent_ids:
            running: dict[str, str | None] = {}
            order: list[str] = []
            # Rolling live-narration state, reset at every turn boundary so a
            # resting agent shows nothing and each turn narrates itself.
            narration_msg: str | None = None  # last completed assistant message
            delta_buf: list[str] = []  # deltas since that message (newer text)
            delta_len = 0
            for ev in self._events.get(aid, ()):
                if ev.kind in ("turn_start", "turn_end", "idle", "aborted"):
                    running.clear()
                    order.clear()
                    narration_msg = None
                    delta_buf = []
                    delta_len = 0
                    continue
                if ev.kind == "assistant_message":
                    if ev.text:
                        narration_msg = ev.text
                    delta_buf = []
                    delta_len = 0
                    continue
                if ev.kind == "assistant_delta":
                    # Accumulate just enough of the streaming message to recover
                    # its first line; stop once we clearly have it so a long
                    # answer can't make this scan quadratic across polls.
                    if ev.text and delta_len < 400:
                        delta_buf.append(ev.text)
                        delta_len += len(ev.text)
                    continue
                rid = ev.request_id
                if not rid:
                    continue
                if ev.tool_status == "running":
                    if rid not in running:
                        order.append(rid)
                    running[rid] = ev.tool_name
                elif ev.tool_status in ("done", "error"):
                    running.pop(rid, None)
                    if rid in order:
                        order.remove(rid)
            # In-flight deltas are newer than the last completed message, so
            # prefer them; fall back to the last full message otherwise.
            raw_narration = "".join(delta_buf) if delta_buf else narration_msg
            narration = _clean_narration(raw_narration) if raw_narration else None
            live = self._live.get(aid)
            pending: dict[str, Any] | None = None
            if live is not None and live.pending_info:
                info = next(iter(live.pending_info.values()))
                pending = {
                    "request_id": info.get("request_id"),
                    "title": info.get("title"),
                    # The whole payload travels: the workflow board renders the
                    # decision card from it, and it has no event stream to mine.
                    "data": dict(info),
                }
            out[aid] = {
                "active_tool": running.get(order[-1]) if order else None,
                "active_tool_count": len(order),
                "active_narration": narration,
                "pending_permission": pending,
            }
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
        await self._ensure_loaded(agent_id)
        live = self._live.get(agent_id)
        events = list(self._events.get(agent_id, []))
        if not events:
            # Nothing archived (neither in memory nor the DB) — e.g. a session
            # resumed after a restart that hasn't re-emitted yet. Fall back to
            # whatever the live session can replay.
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
            events = [normalize_event(ev) for ev in raw or []]
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
            self._loaded.discard(agent_id)
            self._event_locks.pop(agent_id, None)
            self._live_locks.pop(agent_id, None)
            self._auth_announced.pop(agent_id, None)
            # SQLite doesn't enforce ON DELETE CASCADE unless the foreign_keys
            # pragma is on, so clear the archive explicitly (the codebase manages
            # such cleanups in the app layer — see roles/topics delete).
            async with SessionLocal() as session:
                await session.execute(
                    delete(AgentEventRecord).where(AgentEventRecord.agent_session_id == agent_id)
                )
                await session.commit()

    # ------------------------------------------------------------------ commands

    async def clear_session(self, agent_id: int, *, keep_id: bool = False) -> None:
        """Erase an agent's conversation and start its SDK context from scratch.

        Disconnects + forgets the live session and wipes the archived timeline
        (``teardown_session(forget=True)``), clears the agent's published
        artifacts (a cleared context should also freshen the blackboard, so a
        ``/clear`` doesn't leave stale deliverables from the discarded run beside
        the new one), then resets the in-flight/status fields back to idle so the
        next message opens a brand-new SDK session with no prior history resumed.

        ``keep_id`` selects what happens to the public handle:

        * ``False`` (default, interactive ``/clear``) mints a **fresh**
          ``copilot_session_id`` — a brand-new, shareable conversation.
        * ``True`` keeps the **same** ``copilot_session_id`` and instead deletes
          the SDK's on-disk state for it, so a scheduled ``/agent <uuid>``
          reference (which targets that id) keeps resolving while still getting a
          clean context on the next turn. Without the delete, reusing the id
          would resume the old transcript from disk and defeat the clear.
        """
        old_id: str | None = None
        if keep_id:
            agent = await self._load(agent_id)
            old_id = agent.copilot_session_id if agent else None

        await self.teardown_session(agent_id, forget=True)

        # A freshened context starts from a clean blackboard too: drop the prior
        # run's artifacts so a `/clear` (or a scheduled rerun via `keep_id`)
        # doesn't leave stale deliverables next to the new turn's outputs. The
        # trailing `_publish` re-broadcasts `agent.changed`, so the sidebar and
        # in-chat deliverables refetch and empty on their own.
        await self._clear_artifacts(agent_id)

        patch: dict[str, Any] = {
            "status": "idle",
            "active_prompt": None,
            "result_summary": None,
            "error": None,
        }
        if keep_id:
            # Best-effort: a never-connected (pending) agent has nothing on disk.
            if old_id and self._client is not None:
                with contextlib.suppress(Exception):
                    await self._client.delete_session(old_id)
        else:
            patch["copilot_session_id"] = str(uuid.uuid4())
        await self._patch(agent_id, **patch)
        await self._publish(agent_id)

    async def rerun_task(self, agent_id: int, *, extra: str | None = None) -> None:
        """Reset the agent's context (same uuid) and replay its stored task.

        Backs the scheduled ``/agent <uuid> /run`` nudge: instead of the schedule
        re-sending the full instruction block every run (and replaying an
        ever-growing transcript), the instructions live **once** in the agent's
        ``task_prompt``. Each run wipes the prior transcript via
        :meth:`clear_session` (``keep_id=True`` so the schedule's uuid keeps
        resolving), then re-delivers ``task_prompt`` — optionally with an
        ``extra`` one-off note appended for this run — as a clean turn.
        """
        await self.clear_session(agent_id, keep_id=True)
        agent = await self._load(agent_id)
        if agent is None:
            return
        prompt = (agent.task_prompt or "").strip()
        if extra:
            extra = extra.strip()
            prompt = f"{prompt}\n\n{extra}" if prompt else extra
        if not prompt:
            return
        await self.send_message(agent_id, prompt)

    async def run_command(self, agent_id: int, name: str, argument: str) -> None:
        """Execute a system slash command for an agent (never forwarded to the SDK).

        Dispatches to a handler from :attr:`_COMMAND_HANDLERS` (rename/clear/
        archive). The visible feedback is the state change itself (header title,
        empty transcript, the session leaving the list). Raises
        :class:`ValueError` for bad usage or an unknown command so the caller can
        surface it. Adding a command is a single registry entry below.
        """
        handler = self._COMMAND_HANDLERS.get(name)
        if handler is None:
            supported = ", ".join(f"/{cmd}" for cmd in self.supported_commands())
            raise ValueError(
                f"/{name} isn't available in agent sessions — only {supported} are supported."
            )
        await handler(self, agent_id, argument)

    async def _cmd_rename(self, agent_id: int, argument: str) -> None:
        title = " ".join(argument.split())[:200]
        if not title:
            raise ValueError("Usage: /rename <new title>")
        await self._patch(agent_id, title=title)
        await self._publish(agent_id)

    async def _cmd_archive(self, agent_id: int, argument: str) -> None:
        agent = await self._load(agent_id)
        if agent is not None and agent.archived_at is None:
            await self._patch(agent_id, archived_at=datetime.now(UTC))
            await self._publish(agent_id)

    async def _cmd_clear(self, agent_id: int, argument: str) -> None:
        await self.clear_session(agent_id)

    async def _cmd_memory_store(self, agent_id: int, argument: str) -> None:
        from precursor.backend.services import memories as memory_service

        payload = memory_service.parse_store_arg(argument)
        async with SessionLocal() as session:
            await memory_service.create_memory(session, payload)

    async def _cmd_memory_update(self, agent_id: int, argument: str) -> None:
        from precursor.backend.services import memories as memory_service

        memory_id, payload = memory_service.parse_update_arg(argument)
        async with SessionLocal() as session:
            try:
                await memory_service.update_memory(session, memory_id, payload)
            except LookupError as exc:
                raise ValueError(str(exc)) from exc

    # Registry of system slash commands available inside an agent session:
    # name -> async handler. The set of supported names (used for validation and
    # the rejection message) is derived from these keys, and the frontend picker
    # mirrors it via AGENT_SLASH_COMMANDS, so a new command is a single entry.
    _COMMAND_HANDLERS: ClassVar[dict[str, Callable[[AgentManager, int, str], Awaitable[None]]]] = {
        "rename": _cmd_rename,
        "archive": _cmd_archive,
        "clear": _cmd_clear,
        "memory-store": _cmd_memory_store,
        "memory-update": _cmd_memory_update,
    }

    @classmethod
    def supported_commands(cls) -> tuple[str, ...]:
        """Names of slash commands an agent session accepts (registry keys)."""
        return tuple(cls._COMMAND_HANDLERS)

    async def _publish(self, agent_id: int) -> None:
        """Emit an ``agent.changed`` signal for an agent by id (loads its links)."""
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )

    async def _available_model_ids(self) -> set[str]:
        """Model ids the runtime currently offers (empty when unavailable).

        Guards against a stale persisted default: the SDK's catalogue rotates
        over time, so a model that was valid when it was saved can vanish, and
        passing a now-unknown id to ``create_session`` fails the whole turn.
        """
        return {m["id"] for m in await self.list_models()}

    async def _sanitize_model(self, agent_id: int, model: str) -> str:
        """Return ``model`` if the runtime still offers it, else ``"auto"``.

        Only downgrades when we actually have a catalogue to check against: an
        empty catalogue (runtime momentarily down) leaves the selection intact
        so we never mask a transient failure as a model change. ``"auto"`` is
        always accepted, so it's the safe fallback for a vanished pin.
        """
        if not model or model == "auto":
            return model
        available = await self._available_model_ids()
        if available and model not in available:
            logger.warning(
                "agent %s: model %r is no longer offered by the runtime — falling back to 'auto'",
                agent_id,
                model,
            )
            return "auto"
        return model

    @staticmethod
    def _blocked_on_missing_auth(agent: AgentSession, live: _LiveSession) -> list[str]:
        """Servers this agent *explicitly* asked for but couldn't authenticate.

        Restricted to an explicit allowlist on purpose. An operator who named a
        server in a workflow step's ``mcp_servers`` stated a hard requirement:
        running the step without it produces a confident answer improvised from
        the model's own knowledge, which the workflow then records as a success.
        An agent left on the whole enabled catalogue made no such claim, and
        hard-blocking it on one lapsed credential would be a regression.

        Returns human-facing labels, collapsed per credential so a pair sharing
        one sign-in reads as one thing to fix.
        """
        scope = parse_mcp_scope(agent.mcp_servers)
        if not scope:
            return []
        from precursor.backend.services.mcp.oauth_registry import (
            collapse_by_credential,
            server_label,
        )

        missing = [name for name, _ in live.mcp_auth_skipped if name in scope]
        return [server_label(name) for name in collapse_by_credential(sorted(missing))]

    async def _block_turn(self, agent_id: int, labels: list[str]) -> None:
        """Park the agent as ``blocked`` instead of dispatching a tool-less turn.

        Like :meth:`_fail_turn` this lands *outside* the SDK event seam — no turn
        is ever sent, so nothing will emit an event — hence the explicit workflow
        advance. ``blocked`` is what pauses the run (rather than letting the step
        complete on an ``idle`` the agent reached without ever calling a tool),
        and the question tells the user exactly which sign-in unblocks it.
        """
        joined = ", ".join(labels)
        question = (
            f"This step needs {joined}, but the sign-in has expired. Re-authenticate, then resume."
        )
        logger.info("agent %s: blocking turn — no credentials for %s", agent_id, joined)
        await self._patch(agent_id, status="blocked", blocked_question=question, error=None)
        await self._publish(agent_id)
        self.enqueue(self._advance_workflows(agent_id))

    async def _fail_turn(self, agent_id: int, exc: BaseException) -> None:
        """Mark a turn as failed so a dispatch error is visible, not a silent hang.

        Turn dispatch runs as a detached background task (:meth:`enqueue`), so an
        exception there would otherwise be swallowed and leave the agent stuck on
        its "sending…" spinner. Record it as a ``failed`` status with the error
        text and publish, mirroring how the SDK's own ``ErrorData`` is surfaced.

        This lands *outside* the event seam, so the workflow advance is enqueued
        here explicitly: a step whose dispatch blew up emits no further events,
        and without this its run would sit in ``running`` until the watchdog (if
        one is configured at all) noticed.
        """
        logger.exception("agent %s: turn dispatch failed", agent_id)
        message = str(exc).strip() or exc.__class__.__name__
        with contextlib.suppress(Exception):
            await self._patch(agent_id, status="failed", error=message[:2000])
            await self._publish(agent_id)
            self.enqueue(self._advance_workflows(agent_id))

    async def list_models(self) -> list[dict[str, Any]]:
        """Return the runtime's available models, or empty.

        Used to populate the model picker. Surfaces each model's context window
        and advertised reasoning-effort set so the composer can adapt its
        controls. The SDK caches the result after the first call.
        """
        if not self._ready or self._client is None:
            return []
        try:
            models = await self._client.list_models()
        except Exception:
            logger.debug("list_models failed", exc_info=True)
            return []
        out: list[dict[str, Any]] = []
        for m in models or []:
            mid = getattr(m, "id", None)
            if not mid:
                continue
            caps = getattr(m, "capabilities", None)
            limits = getattr(caps, "limits", None) if caps is not None else None
            ctx = None
            if limits is not None:
                ctx = getattr(limits, "max_prompt_tokens", None) or getattr(
                    limits, "max_context_window_tokens", None
                )
            efforts = getattr(m, "supported_reasoning_efforts", None) or []
            out.append(
                {
                    "id": str(mid),
                    "name": str(getattr(m, "name", None) or mid),
                    "context_window": int(ctx) if isinstance(ctx, (int, float)) else None,
                    "supported_reasoning_efforts": [str(e) for e in efforts],
                }
            )
        return out

    async def _apply_agent_model(
        self,
        agent: AgentSession,
        live: _LiveSession,
        *,
        default_model: str,
        effort: str,
        tier: str,
    ) -> None:
        """``set_model`` a single idle live agent to its selected model.

        The model is ``agent.model or default_model`` — an explicit per-agent pin
        wins, otherwise the current composer/Settings selection applies. History
        preserving and effective on the agent's next turn. No-op when the target
        (model, effort, tier) already matches what we last applied.
        """
        model = agent.model or default_model
        if not model:
            return
        signature = (model, effort or None, tier or "default")
        if live.model_signature == signature:
            return
        # Always send the tier (incl. "default") so toggling back resets it;
        # a falsy effort is sent as None so the runtime restores the model
        # default rather than pinning a stale level.
        kwargs: dict[str, Any] = {"context_tier": tier or "default"}
        if effort:
            kwargs["reasoning_effort"] = effort
        try:
            await live.sdk_session.set_model(model, **kwargs)
            live.model_signature = signature
        except Exception:
            logger.debug("set_model failed for agent %s", agent.id, exc_info=True)

    async def _sync_selected_model(self, agent: AgentSession) -> None:
        """Reconcile ``agent``'s live session to the current model selection.

        Called right before a turn is dispatched so every next turn follows the
        composer/Settings selection, even on a long-lived reused session. Skipped
        when there's no live session yet (a fresh build already bakes in the
        selection).
        """
        live = self._live.get(agent.id)
        if live is None:
            return
        async with SessionLocal() as s:
            default_model = await resolve_agents_default_model(s)
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
        await self._apply_agent_model(
            agent, live, default_model=default_model, effort=effort, tier=tier
        )

    async def apply_session_overrides(self) -> None:
        """Apply the current global model / reasoning-effort / context-tier prefs
        onto idle live sessions.

        Lets a change in the composer (or Settings → Agents) take effect on the
        next message of an in-progress conversation instead of only new sessions.
        Uses the SDK's ``set_model`` — history-preserving, effective next turn.
        Skips sessions with a turn in flight, where switching the model is unsafe;
        those pick the change up on their next idle dispatch.
        """
        if not self._ready:
            return
        live_ids = list(self._live.keys())
        if not live_ids:
            return
        async with SessionLocal() as s:
            default_model = await resolve_agents_default_model(s)
            effort = await resolve_agents_reasoning_effort(s)
            tier = await resolve_agents_context_tier(s)
            rows = (
                (await s.execute(select(AgentSession).where(AgentSession.id.in_(live_ids))))
                .scalars()
                .all()
            )
        by_id = {a.id: a for a in rows}
        for agent_id in live_ids:
            live = self._live.get(agent_id)
            agent = by_id.get(agent_id)
            if live is None or agent is None:
                continue
            if agent.status in {"running", "needs_approval", "pending"}:
                continue
            await self._apply_agent_model(
                agent, live, default_model=default_model, effort=effort, tier=tier
            )

    def _make_permission_handler(self, agent_id: int) -> Any:
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
                live = self._live.get(agent_id)
                policy = (
                    live.approval_policy if live else None
                ) or get_settings().agents_approval_policy
                logger.info(
                    "agent %s: permission handler hit — request=%s policy=%s live=%s",
                    agent_id,
                    req_name,
                    policy,
                    live is not None,
                )
                if policy == "autonomous":
                    logger.info("agent %s: %s auto-approved (autonomous)", agent_id, req_name)
                    return self._approve_once()
                if policy != "manual" and should_auto_approve(request):
                    logger.info("agent %s: %s auto-approved (read-only)", agent_id, req_name)
                    return self._approve_once()
                info = describe_permission(request)
                # Honour a prior "approve for session" for the same action.
                if live is not None and permission_signature(info) in live.session_approvals:
                    logger.info("agent %s: %s auto-approved (session grant)", agent_id, req_name)
                    return self._approve_once()
                logger.info(
                    "agent %s: %s requires approval — parking (%s)",
                    agent_id,
                    req_name,
                    info.get("title"),
                )
                return await self._park_permission(agent_id, request, info)
            except asyncio.CancelledError:
                raise
            except Exception:
                fallback = get_settings().agents_approval_policy
                logger.exception(
                    "agent %s: permission handler failed for %s; falling back to %r policy",
                    agent_id,
                    req_name,
                    fallback,
                )
                # Don't silently deny in unattended modes — that's the bug we're
                # guarding against. Manual mode can't safely auto-approve, so emit
                # an explicit rejection the UI can show rather than a crash.
                if fallback != "manual":
                    return self._approve_once()
                return self._reject("permission handler error")

        return handler

    async def _approval_policy(self, agent: AgentSession | None = None) -> str:
        # A per-agent override wins over the global default when it's a valid
        # policy; ``None``/unset falls through to the DB-backed global setting.
        override = getattr(agent, "approval_policy", None)
        if override in AGENTS_APPROVAL_POLICIES:
            return str(override)
        try:
            async with SessionLocal() as session:
                return await resolve_agents_approval_policy(session)
        except Exception:
            fallback = get_settings().agents_approval_policy
            logger.warning(
                "agent: approval-policy DB read failed; using in-memory default %r",
                fallback,
                exc_info=True,
            )
            return fallback

    async def _park_permission(
        self, agent_id: int, request: Any, info: dict[str, Any] | None = None
    ) -> Any:
        live = self._live.get(agent_id)
        if live is None:
            logger.warning(
                "agent %s: cannot park permission — no live session; rejecting", agent_id
            )
            return self._reject("session gone")
        request_id = str(getattr(request, "tool_call_id", "") or id(request))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        live.pending[request_id] = fut
        live.pending_info[request_id] = {
            "request_id": request_id,
            **(info if info is not None else describe_permission(request)),
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

    async def _ensure_loaded(self, agent_id: int) -> None:
        """Hydrate the in-memory timeline from the ``agent_events`` archive once.

        After a process restart the live cache is empty and the SDK only replays
        ``SessionStartData`` on resume, so the durable history lives only in the
        DB. Load it lazily the first time an agent is touched (an event arriving
        or a timeline read) and mark it loaded so we don't re-read per event.
        """
        if agent_id in self._loaded:
            return
        async with self._events_lock:
            if agent_id in self._loaded:
                return
            async with SessionLocal() as session:
                payloads = (
                    await session.scalars(
                        select(AgentEventRecord.payload)
                        .where(AgentEventRecord.agent_session_id == agent_id)
                        .order_by(AgentEventRecord.id)
                    )
                ).all()
            archived: list[AgentEvent] = []
            for payload in payloads:
                try:
                    archived.append(AgentEvent.model_validate_json(payload))
                except Exception:
                    logger.debug(
                        "skipping malformed archived event for agent %s", agent_id, exc_info=True
                    )
            if archived:
                self._events[agent_id] = archived
            self._loaded.add(agent_id)

    async def _archive_event(self, agent_id: int, event: AgentEvent) -> None:
        """Persist one normalised event to the durable timeline archive."""
        try:
            async with SessionLocal() as session:
                session.add(
                    AgentEventRecord(
                        agent_session_id=agent_id,
                        payload=event.model_dump_json(),
                    )
                )
                await session.commit()
        except Exception:
            logger.debug("failed to archive event for agent %s", agent_id, exc_info=True)

    async def _handle_event(self, agent_id: int, event: Any) -> None:
        # Serialise per-agent so events are handled in arrival order: the idle
        # handler must run *after* the assistant-message handler has committed
        # ``result_summary``, otherwise ``_notify_back`` posts the previous
        # turn's answer.
        lock = self._event_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            await self._handle_event_locked(agent_id, event)

    async def _handle_event_locked(self, agent_id: int, event: Any) -> None:
        # Archive every event so the timeline persists across session teardown
        # (e.g. on topic link) and process restart, where the SDK would otherwise
        # drop it (``get_events`` only replays ``SessionStartData`` on resume).
        await self._ensure_loaded(agent_id)
        # The status *before* this event is handled, read up front rather than
        # around the final patch below: handlers reached from here (``_on_idle``,
        # and ``_enforce_budget`` via ``_record_usage``) commit status changes of
        # their own mid-flight, and those are transitions the workflow seam must
        # still see.
        before = await self._load(agent_id)
        before_status = before.status if before is not None else None
        normalised = normalize_event(event)
        normalised.at = datetime.now(UTC)
        self._events.setdefault(agent_id, []).append(normalised)
        await self._archive_event(agent_id, normalised)

        # A workiq tool that errors after the session was built with valid creds
        # usually means the OAuth token lapsed mid-turn. Surface the same sign-in
        # prompt as the pre-flight gate so the user can re-authenticate inline
        # instead of reading a raw tool failure. Best-effort: only fires when the
        # event carries a server name and the creds are actually gone.
        auth_server = await self._auth_server_from_failed_tool(normalised)
        if auth_server is not None:
            await self._announce_auth_required(agent_id, [auth_server])

        data = getattr(event, "data", event)
        name = type(data).__name__
        now = datetime.now(UTC)
        patch: dict[str, Any] = {"last_activity_at": now}

        if name == "AssistantMessageData":
            content = getattr(data, "content", None)
            if content:
                # Scrub control directives from the *displayed* summary; keep the
                # raw message in ``pending_answer`` for the topic repost and for
                # directive/gate parsing downstream.
                patch["result_summary"] = strip_control_directives(str(content))[:2000]
                # Keep the full answer for the topic/chat repost — the summary
                # column is capped for the agent list.
                live = self._live.get(agent_id)
                if live is not None:
                    live.pending_answer = str(content)
        elif name == "AssistantUsageData":
            await self._record_usage(agent_id, data)
        elif name in ("SessionIdleData", "SystemNotificationAgentIdle"):
            agent = await self._load(agent_id)
            # Don't let a trailing idle event mask a turn that just errored, was
            # paused/cancelled, or already reached a terminal/blocked resting
            # state — those statuses are sticky so the outcome stays visible (and
            # any in-flight prompt stays resumable).
            if agent is not None and agent.status not in (
                "needs_approval",
                "cancelled",
                "failed",
                "blocked",
                "completed",
            ):
                # The goal loop decides the resting status (idle/blocked/completed)
                # or continues autonomously; it mutates ``patch`` and reposts only
                # at rest transitions.
                await self._on_idle(agent, patch)
        elif name in ("AbortData",):
            patch["status"] = "cancelled"
            patch["finished_at"] = datetime.now(UTC)
        elif name in ("ErrorData", "SessionErrorData"):
            patch["status"] = "failed"
            patch["error"] = str(getattr(data, "message", name))[:2000]
            patch["finished_at"] = datetime.now(UTC)
            # Auto-recovery: if a retry budget remains, arm a backoff re-run the
            # scheduler will pick up. Keeps a flaky turn from parking the fleet.
            failed_agent = await self._load(agent_id)
            if failed_agent is not None and failed_agent.retry_count < failed_agent.max_retries:
                patch["next_retry_at"] = self._retry_due_at(failed_agent.retry_count)

        await self._patch(agent_id, **patch)
        agent = await self._load(agent_id)
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id if agent else None,
            chat_id=agent.chat_id if agent else None,
        )
        # Workflow coordination advances when this agent *transitions into* a
        # resting/terminal state — a workflow chains plain agents itself. Testing
        # the status alone would re-fire for every subsequent event an already
        # resting agent emits (pending-message, MCP-status and tool-list updates
        # all arrive after a turn ends), and each of those advances raced the
        # others into re-entering the same step: duplicate trace rows, a second
        # real ``start_task``, and double-counted tokens.
        if (
            agent is not None
            and agent.status != before_status
            and agent.status in _RESTING_STATUSES
        ):
            self.enqueue(self._advance_workflows(agent_id))

    async def _record_usage(self, agent_id: int, data: Any) -> None:
        """Meter an ``AssistantUsageData`` round into the shared usage ledger.

        Each agent LLM call lands as one ``source="agent"`` row tagged with the
        agent's linked container, so agent spend shows up in the global usage
        stats alongside chat/topic turns. ``SessionUsageInfoData`` is *not*
        recorded — it reports context-window occupancy, not billable deltas, so
        counting it would double-charge the turn.
        """
        prompt_tokens = int(getattr(data, "input_tokens", None) or 0)
        completion_tokens = int(getattr(data, "output_tokens", None) or 0)
        if not prompt_tokens and not completion_tokens:
            return
        model = getattr(data, "model", None)
        agent = await self._load(agent_id)
        if agent is None:
            return
        try:
            async with SessionLocal() as session:
                await record_usage(
                    session,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    source="agent",
                    model=str(model) if model else agent.model,
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                )
                await session.commit()
        except Exception:
            logger.debug("failed to record agent usage for %s", agent_id, exc_info=True)

        # Accumulate the running totals on the agent row (drives the budget cap
        # and aggregate observability). Kept in a separate write so a usage-ledger
        # failure above doesn't lose the meter, and vice versa.
        await self._patch(
            agent_id,
            total_input_tokens=agent.total_input_tokens + prompt_tokens,
            total_output_tokens=agent.total_output_tokens + completion_tokens,
        )
        await self._enforce_budget(agent_id)

    async def _enforce_budget(self, agent_id: int) -> None:
        """Park an agent as ``blocked`` once it burns through its token budget.

        The governor is a *soft* cap checked after each metered round: an
        in-flight turn finishes, but the next autonomous step won't start. Null
        budget = ungoverned. Already-terminal/blocked agents are left alone so we
        don't clobber a completion that landed in the same turn.
        """
        agent = await self._load(agent_id)
        if agent is None or agent.token_budget is None:
            return
        spent = agent.total_input_tokens + agent.total_output_tokens
        if spent < agent.token_budget:
            return
        if agent.status not in ("running", "needs_approval"):
            return
        await self._patch(
            agent_id,
            status="blocked",
            active_prompt=None,
            blocked_question=(
                f"I've reached my token budget ({agent.token_budget:,} tokens; "
                f"{spent:,} spent). Review my progress and raise the budget or "
                "adjust the objective to continue."
            ),
        )
        await publish_agent_changed(
            agent_session_id=agent_id,
            topic_id=agent.topic_id,
            chat_id=agent.chat_id,
        )

    async def _on_idle(self, agent: AgentSession, patch: dict[str, Any]) -> None:
        """Resolve what a finished turn means for an agent's mission.

        This is the goal loop. For a **plain** agent it just rests at ``idle`` and
        reposts the exchange (unchanged behaviour). For an **autonomous** agent it
        reads the control directives the model embedded in its last message and:

        * ``OBJECTIVE_COMPLETE`` → terminal ``completed`` (one final repost);
        * ``NEED_INPUT`` → ``blocked`` on the raised question (repost);
        * otherwise, if the step budget remains and it isn't stalling, it keeps
          going — status stays ``running``, the step count ticks up, and the next
          step is enqueued with **no** repost (so a whole multi-step mission lands
          as a single objective→result exchange when it finally rests);
        * budget exhausted or stalled → ``blocked`` for a human to course-correct.

        Progress (``PROGRESS: n | label``) is applied whenever present and resets
        the stall counter on a fresh value. Mutates ``patch`` in place.
        """
        live = self._live.get(agent.id)
        directives = (
            parse_agent_directives(live.pending_answer)
            if live is not None and agent.autonomy_enabled
            else {}
        )
        if live is not None:
            live.directive = directives or None

        # A progress report applies regardless of the terminal decision below, and
        # a *new* value clears the stall counter (the agent is genuinely moving).
        progress = directives.get("progress")
        if progress is not None:
            patch["progress"] = progress["value"]
            patch["progress_label"] = progress["label"]
            if live is not None and progress["value"] != live.last_progress:
                live.last_progress = progress["value"]
                live.stall_count = 0

        # Published artifacts land on the blackboard regardless of the terminal
        # decision below, so mid-mission outputs are shared as soon as they're
        # emitted (not only at completion).
        artifacts = directives.get("artifacts")
        if artifacts:
            await self._persist_artifacts(agent.id, artifacts, kind="output")

        # 1) Objective met — terminal. The single repost carries the summary.
        if directives.get("complete"):
            # The OBJECTIVE_COMPLETE reason is a *meta* description of the work
            # ("Told the user a joke"); the actual deliverable is the message's
            # prose — the joke itself. Prefer that prose as the displayed result,
            # falling back to the reason only when the final turn was
            # directives-only (a bare OBJECTIVE_COMPLETE with no body).
            reason = strip_control_directives(str(directives["complete"]))[:2000]
            body = (
                strip_control_directives(str(live.pending_answer))[:2000]
                if live is not None and live.pending_answer
                else ""
            )
            summary = body or reason
            patch["status"] = "completed"
            patch["result_summary"] = summary
            patch["active_prompt"] = None
            patch["progress"] = 100
            patch["step_count"] = 0
            patch["finished_at"] = datetime.now(UTC)
            if live is not None:
                live.stall_count = 0
            # Auto-capture the outcome as a result artifact so downstream agents
            # get the deliverable even when the model didn't emit an ARTIFACT line.
            if summary:
                await self._persist_artifacts(
                    agent.id, [{"title": "Result", "content": summary}], kind="result"
                )
            await self._notify_back(agent)
            return

        # 2) Agent raised a decision only the human can make — park it.
        if directives.get("blocked"):
            patch["status"] = "blocked"
            patch["blocked_question"] = str(directives["blocked"])[:2000]
            patch["active_prompt"] = None
            await self._notify_back(agent)
            return

        # 3) Autonomous continuation — keep pursuing the objective if allowed.
        if agent.autonomy_enabled:
            if live is not None and live.stall_count >= _STALL_LIMIT:
                patch["status"] = "blocked"
                patch["blocked_question"] = (
                    "I've taken several steps without measurable progress toward "
                    "the objective. Please review and advise on how to proceed."
                )
                patch["active_prompt"] = None
                await self._notify_back(agent)
                return
            if agent.step_count < agent.max_steps:
                # Keep going. No repost — the mission is still in flight; the
                # single objective→result exchange lands when it rests.
                patch["status"] = "running"
                patch["step_count"] = agent.step_count + 1
                # No progress advance this step counts toward a stall.
                if live is not None and progress is None:
                    live.stall_count += 1
                self.enqueue(self._advance_goal_loop(agent.id))
                return
            # Budget spent without completing — hand back rather than run forever.
            patch["status"] = "blocked"
            patch["blocked_question"] = (
                f"I've reached the step budget ({agent.max_steps} steps) for this "
                "objective without completing it. Review my progress and tell me "
                "whether to continue, adjust the objective, or stop."
            )
            patch["active_prompt"] = None
            await self._notify_back(agent)
            return

        # 4) Plain agent: rest at idle and repost this turn's exchange.
        patch["status"] = "idle"
        patch["active_prompt"] = None
        await self._notify_back(agent)

    async def _advance_goal_loop(self, agent_id: int) -> None:
        """Take the next autonomous step toward the objective.

        Enqueued (never called inline) from the idle handler — we must not
        ``send`` from inside the locked event handler. Reloads the agent,
        re-checks it's still an autonomous run that should continue, refreshes the
        per-turn approval policy, and nudges the SDK session to keep working.
        ``pending_prompt`` is deliberately left untouched so the eventual single
        repost still shows the objective + final answer.
        """
        try:
            agent = await self._load(agent_id)
            if agent is None or not agent.autonomy_enabled or agent.status != "running":
                return
            live = self._live.get(agent_id)
            if live is None:
                return
            live.approval_policy = await self._approval_policy(agent)
            # Reset the per-turn answer buffer so the next directive parse reads
            # only the upcoming step's message.
            live.pending_answer = None
            await live.sdk_session.send(_CONTINUE_NUDGE)
        except Exception as exc:
            await self._fail_turn(agent_id, exc)

    async def _notify_back(self, agent: AgentSession) -> None:
        """Post the just-finished turn's exchange into the linked container.

        Posts the turn's **prompt** (as a user turn) and the agent's **answer**
        (as an assistant turn), both tagged with ``agent_session_id`` so the UI
        renders an "agent exchange" badge linking back to ``/agents/{id}``. Like
        the reminder ticker, the discussion goes unread + notifies.

        Posts **once per turn**: the prompt is captured on ``_LiveSession`` when a
        task/follow-up is sent and cleared here, so repeated idle events for the
        same turn don't double-post and every turn (not just the first) lands in
        the topic. A resumed turn with no tracked prompt is skipped.
        """
        if agent.topic_id is None and agent.chat_id is None:
            return

        live = self._live.get(agent.id)
        if live is None or live.pending_prompt is None:
            return
        prompt = live.pending_prompt
        live.pending_prompt = None

        # Prefer the full assistant text captured this turn; fall back to the
        # (capped) summary so a resumed turn without a tracked answer still posts.
        answer = (
            live.pending_answer or agent.result_summary or ""
        ).strip() or "Agent task finished."
        live.pending_answer = None
        answer, suggestions = split_suggestions(answer)
        now = datetime.now(UTC)
        # Keep the posted messages strictly newer than any last_read_at we pin,
        # so the unread badge lights up reliably (mirrors the reminder ticker).
        read_threshold = now - timedelta(seconds=1)
        async with SessionLocal() as session:
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.USER,
                    content=prompt,
                    agent_session_id=agent.id,
                    created_at=now,
                )
            )
            session.add(
                Message(
                    topic_id=agent.topic_id,
                    chat_id=agent.chat_id,
                    role=MessageRole.ASSISTANT,
                    content=answer,
                    suggestions=json.dumps(suggestions) if suggestions else None,
                    agent_session_id=agent.id,
                    created_at=now,
                )
            )
            # Ensure the linked conversation reads as unread even when it was
            # never opened: last_read_at IS NULL is treated as fully read, so the
            # agent's reply wouldn't count. Pin last_read just before the messages
            # when null (or somehow stamped in the future) without masking other
            # genuinely-unread history. Mirrors services/reminders.py.
            container: Topic | Chat | None = None
            if agent.topic_id is not None:
                container = await session.get(Topic, agent.topic_id)
            elif agent.chat_id is not None:
                container = await session.get(Chat, agent.chat_id)
            if container is not None:
                last_read = container.last_read_at
                if last_read is not None and last_read.tzinfo is None:
                    last_read = last_read.replace(tzinfo=UTC)
                if last_read is None or last_read > read_threshold:
                    container.last_read_at = read_threshold
            await session.commit()
        if agent.topic_id is not None:
            await publish_message_changed(agent.topic_id)
        elif agent.chat_id is not None:
            await publish_message_changed_chat(agent.chat_id)

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

    # ---------------------------------------------------------------- fleet ----

    async def _persist_artifacts(
        self, agent_id: int, artifacts: list[dict[str, str]], *, kind: str
    ) -> None:
        """Write published outputs to the shared blackboard (``agent_artifacts``).

        ``kind`` here is the *provenance* — ``"result"`` for the auto-captured
        completion summary, ``"output"`` for a model-emitted ``ARTIFACT:`` line —
        stored on the row's ``key`` so downstream injection and the UI can tell
        them apart. The stored ``kind`` column is a rendering hint (kept as plain
        ``text``). De-duplicates an identical ``result`` so a completion that
        reposts the same summary doesn't stack duplicates. Best-effort: a
        blackboard write must never break the turn.
        """
        from precursor.backend.models.agent_artifact import AgentArtifact

        try:
            async with SessionLocal() as session:
                for art in artifacts:
                    title = (art.get("title") or "Untitled").strip()[:200]
                    content = (art.get("content") or "").strip()[:100000]
                    if not content:
                        continue
                    if kind == "result":
                        existing = await session.execute(
                            select(AgentArtifact.id).where(
                                AgentArtifact.agent_id == agent_id,
                                AgentArtifact.key == "result",
                                AgentArtifact.content == content,
                            )
                        )
                        if existing.first() is not None:
                            continue
                    session.add(
                        AgentArtifact(
                            agent_id=agent_id,
                            key=kind,
                            kind="text",
                            title=title,
                            content=content,
                        )
                    )
                await session.commit()
        except Exception:
            logger.debug("failed to persist artifacts for %s", agent_id, exc_info=True)

    async def _clear_artifacts(self, agent_id: int) -> None:
        """Wipe an agent's published artifacts ahead of a fresh objective run.

        A re-run (manual restart, retry, edited task, a webhook re-trigger, or an
        upstream re-driving an already-completed dependent) should start with a
        clean blackboard so the new turn's outputs replace the previous run's
        rather than accumulating. Best-effort and idempotent — a no-op on first
        run. Deliberately *not* called from :meth:`send_message`: a conversational
        follow-up keeps the existing artifacts.
        """
        from precursor.backend.models.agent_artifact import AgentArtifact

        try:
            async with SessionLocal() as session:
                await session.execute(
                    delete(AgentArtifact).where(AgentArtifact.agent_id == agent_id)
                )
                await session.commit()
        except Exception:
            logger.debug("failed to clear artifacts for %s", agent_id, exc_info=True)

    def _retry_due_at(self, retry_count: int) -> datetime:
        """Next-attempt time with exponential backoff off the base interval."""
        settings = get_settings()
        base = max(1, settings.agents_retry_backoff_seconds)
        delay = base * (2**retry_count)
        return datetime.now(UTC) + timedelta(seconds=delay)

    async def _advance_workflows(self, agent_id: int) -> None:
        """Advance any running workflow whose current step is this agent.

        Enqueued from the completion seam when an agent reaches a resting or
        terminal state. Delegates to the workflow coordinator (imported lazily to
        avoid a circular import) in a fresh session so the advance commits
        independently of the turn that triggered it.
        """
        try:
            from precursor.backend.services.agents import workflow as workflow_svc

            async with SessionLocal() as session:
                await workflow_svc.advance_for_agent(session, self, agent_id)
        except Exception:
            logger.debug("failed to advance workflows after %s", agent_id, exc_info=True)

    async def release_ready_fleet(self) -> None:
        """Sweep for orphaned ``pending`` agents and start them.

        ``pending`` is a brief transient: :func:`_spawn_agent` creates an agent
        ``pending`` and enqueues its ``start_task``. This backstop starts any
        ``pending`` agent whose ``start_task`` was lost (e.g. the app restarted
        mid-spawn), respecting the concurrency governor. ``waiting`` agents are
        parked deliberately for a manual/webhook trigger and are never swept.
        """
        if not self.ready:
            return
        try:
            settings = get_settings()
            async with SessionLocal() as session:
                rows = await session.execute(
                    select(AgentSession.id).where(
                        AgentSession.status == "pending",
                        AgentSession.archived_at.is_(None),
                    )
                )
                candidates = [int(r) for r in rows.scalars().all()]
            for agent_id in candidates:
                async with SessionLocal() as session:
                    if await fleet.running_count(session) >= settings.agents_max_concurrent:
                        break
                await self.start_task(agent_id)
        except Exception:
            logger.debug("fleet sweep failed", exc_info=True)

    async def retry_agent(self, agent_id: int) -> None:
        """Re-run a failed agent, counting the attempt against its retry budget.

        Invoked by the scheduler when ``next_retry_at`` comes due. Clears the
        retry arming and error, bumps ``retry_count``, then replays the task.
        """
        agent = await self._load(agent_id)
        if agent is None or agent.status != "failed":
            return
        if agent.retry_count >= agent.max_retries:
            return
        await self._patch(
            agent_id,
            retry_count=agent.retry_count + 1,
            next_retry_at=None,
            error=None,
        )
        await self.restart_with_task(agent_id)


# Map SDK event class names → coarse workflow step kinds for the UI.
_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
