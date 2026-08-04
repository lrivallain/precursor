"""Track when each OAuth credential was last actually used.

The keep-alive loop refreshes tokens shortly before they expire so an active
session never breaks mid-turn. Left ungated that is indiscriminate: a WorkIQ
server the user enabled months ago and never calls still gets refreshed, and
when its refresh token finally lapses the loop raises a sign-in prompt for
tools nobody asked for. That is a meaningful share of the prompt noise once
several WorkIQ servers are enabled.

Usage is recorded per *credential*, not per server, because servers sharing a
token (the Agent 365 pair) are kept warm together — using either one keeps the
shared credential alive.

State is deliberately process-local and in-memory: marking every tool call in
the database would add a write to a hot path for data that only matters while
the process is running. To avoid the "cold after restart" trap, the process
start time seeds the clock, so a freshly started app keeps every credential
warm for one idle window rather than going quiet until the first tool call.
"""

from __future__ import annotations

import time

from precursor.backend.services.mcp.oauth_registry import credential_key

_started_at: float = time.monotonic()
_last_used: dict[str, float] = {}


def mark_server_used(server: str) -> None:
    """Record that ``server`` was just called, keeping its credential warm."""
    _last_used[credential_key(server)] = time.monotonic()


def seconds_since_use(credential: str) -> float:
    """Seconds since ``credential`` was last used, or since process start."""
    return time.monotonic() - max(_last_used.get(credential, 0.0), _started_at)


def is_idle(credential: str, idle_after_seconds: int) -> bool:
    """Whether ``credential`` has gone unused long enough to stop keeping warm.

    ``idle_after_seconds <= 0`` disables the gate, restoring the previous
    always-keep-warm behaviour.
    """
    if idle_after_seconds <= 0:
        return False
    return seconds_since_use(credential) > idle_after_seconds


def reset_usage() -> None:
    """Clear tracked usage and restart the idle clock (tests, app restart)."""
    global _started_at
    _last_used.clear()
    _started_at = time.monotonic()
