"""Per-session MCP server scoping: the ``mcp_servers`` allowlist.

Agents, runs and workflow steps can each narrow which MCP servers a session
sees. The manager's attach path, the workflow coordinator and workflow
import/export all read that allowlist, so the parse/normalise pair lives in this
leaf module rather than in ``services.agents.manager``, which it must never
import.
"""

from __future__ import annotations

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


#: Ceiling for a stored scope, matching the ``mcp_servers`` column width on
#: ``AgentSession``, ``AgentRun`` and ``WorkflowStep``.
MCP_SCOPE_MAX_LEN = 400


def normalize_mcp_scope(raw: str | None) -> str | None:
    """Tidy an ``mcp_servers`` allowlist for storage without collapsing its empty case.

    Deliberately *not* the ``(x or "").strip() or None`` idiom used for other
    optional strings: here null and empty mean different things (every enabled
    server versus none at all), so an explicitly empty selection has to survive
    the round-trip. Names are de-duplicated with their order kept, and never
    checked against the local registry — the parse half makes the same promise,
    and an agent or workflow is portable, so a server absent on this machine
    simply matches nothing.

    Paired with :func:`parse_mcp_scope`: everything that writes a scope goes
    through this, everything that reads one goes through that, so the stored and
    effective forms can't drift.
    """
    if raw is None:
        return None
    seen: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name and name not in seen:
            seen.append(name)
    return ",".join(seen)[:MCP_SCOPE_MAX_LEN]


#: The built-in server, attached from :meth:`MCPConfigBuilder.precursor_mcp_config`
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
