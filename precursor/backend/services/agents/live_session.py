"""The per-run live SDK session record and the capability-source alias.

A leaf module so the manager's collaborators can type against ``_LiveSession``
without importing ``manager``, which must never be imported from here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from precursor.backend.models import AgentRun, AgentSession

#: Anything carrying the capability toggles a live session is built from: the
#: executing ``AgentRun``'s immutable snapshot, or the ``AgentSession`` itself
#: when a caller has no run in hand.
_Caps = AgentRun | AgentSession


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
