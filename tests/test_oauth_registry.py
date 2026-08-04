"""OAuth server registry — credential identity, collapsing and usage tracking.

The registry is the single place that answers "which MCP servers sign in, and
which of them share one credential". Getting that wrong is what produced the
duplicate sign-in prompts, so these tests pin the collapsing rules and the idle
back-off clock that the keep-alive loop reads.
"""

from __future__ import annotations

from precursor.backend.services.mcp import agent365
from precursor.backend.services.mcp.oauth_registry import (
    OAUTH_SERVER_NAMES,
    collapse_by_credential,
    credential_key,
    is_oauth_server,
    server_label,
)
from precursor.backend.services.mcp.usage import (
    is_idle,
    mark_server_used,
    reset_usage,
    seconds_since_use,
)


def test_registry_covers_every_oauth_server() -> None:
    """Preview plus both Agent 365 servers, preview first."""
    assert OAUTH_SERVER_NAMES[0] == "workiq"
    for spec in agent365.AGENT365_SERVERS:
        assert spec.name in OAUTH_SERVER_NAMES
        assert is_oauth_server(spec.name)
    assert not is_oauth_server("github")


def test_agent365_pair_shares_one_credential() -> None:
    """The Teams/User pair authenticates once; the preview stays separate."""
    teams, user = (spec.name for spec in agent365.AGENT365_SERVERS)
    assert credential_key(teams) == credential_key(user)
    assert credential_key("workiq") != credential_key(teams)
    # Non-OAuth servers are their own credential, so they never collapse together.
    assert credential_key("github") == "github"
    assert credential_key("my-http") == "my-http"


def test_collapse_by_credential_keeps_one_name_per_sign_in() -> None:
    """Two servers behind one credential yield one prompt, in first-seen order."""
    teams, user = (spec.name for spec in agent365.AGENT365_SERVERS)
    assert collapse_by_credential([teams, user]) == [teams]
    assert collapse_by_credential([user, teams]) == [user]
    # Distinct credentials all survive, and order is preserved.
    assert collapse_by_credential(["workiq", teams, user, "github"]) == [
        "workiq",
        teams,
        "github",
    ]
    assert collapse_by_credential([]) == []


def test_server_label_is_human_readable() -> None:
    """Prompts name the server the way the UI does, not its slug."""
    assert server_label("workiq") == "WorkIQ"
    assert server_label(agent365.AGENT365_SERVERS[0].name) == "WorkIQ Teams"
    # Unknown servers fall back to their own name rather than blowing up.
    assert server_label("github") == "github"


def test_usage_starts_warm_and_marks_per_credential() -> None:
    """A fresh process is warm, and using either sibling keeps the pair warm."""
    reset_usage()
    teams, user = (spec.name for spec in agent365.AGENT365_SERVERS)
    shared = credential_key(teams)

    # Seeded from process start, so a restart doesn't leave every server cold.
    assert seconds_since_use(shared) >= 0
    assert not is_idle(shared, 3600)

    mark_server_used(user)
    assert not is_idle(shared, 3600)
    # A credential nobody touched is judged on the same seeded clock.
    assert not is_idle(credential_key("workiq"), 3600)


def test_idle_gate_can_be_disabled() -> None:
    """A non-positive window restores the always-keep-warm behaviour."""
    reset_usage()
    # An impossible window: everything is idle...
    assert is_idle(credential_key("workiq"), -1) is False
    assert is_idle(credential_key("workiq"), 0) is False


def test_idle_after_window_elapses(monkeypatch) -> None:
    """Past the window with no calls, the credential stops being kept warm."""
    from precursor.backend.services.mcp import usage

    reset_usage()
    credential = credential_key("workiq")
    base = usage.time.monotonic()
    monkeypatch.setattr(usage.time, "monotonic", lambda: base + 7200)

    assert is_idle(credential, 3600)
    # Any tool call resets the clock immediately.
    mark_server_used("workiq")
    assert not is_idle(credential, 3600)
