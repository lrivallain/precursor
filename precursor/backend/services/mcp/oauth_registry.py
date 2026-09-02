"""Registry of the MCP servers that sign in through Precursor's browser OAuth.

Precursor holds more than one Entra credential: the hosted WorkIQ preview
authenticates as its own public client, and the Agent 365 servers (the
``workiq-*`` family) authenticate as another one against a
different resource. Nothing can collapse those into a single sign-in without
tenant-admin consent, so the code has to treat "which credential does this
server use" as a first-class question.

Before this module every caller answered that question by hand — usually as
``if name == "workiq"``, which silently excluded Agent 365 — and three separate
places grew their own copy of "enumerate every OAuth profile". This is the one
place that knows:

* :func:`is_oauth_server` / :func:`server_label` — cheap, static lookups safe to
  call from sync code and from modules that must not import the OAuth stack.
* :func:`credential_key` — which *credential* a server signs in with. Servers
  sharing a key are one sign-in, so prompting for both is pure noise. Static on
  purpose: the Agent 365 servers share a token by construction, so this answer
  never needs the tenant, the DB or an ``await``.
* :func:`profile_for_server` / :func:`active_profiles` / :func:`unique_credentials`
  — the live :class:`WorkIQOAuthProfile` objects, which *do* need configuration
  (preview toggle, resolved tenant) and so are async.

``credential_key`` mirrors ``OAUTH_AUTH_FAMILIES`` in ``frontend/src/lib/
mcpServers.ts``; the two must stay in step or the SPA and the backend will
disagree about how many prompts a lapse is worth.
"""

from __future__ import annotations

import logging
from typing import Final

from precursor.backend.services.mcp import agent365, workiq_preview
from precursor.backend.services.mcp.agent365 import (
    AGENT365_SERVERS,
    AGENT365_TOKENS_KEY,
)
from precursor.backend.services.mcp.workiq_preview import (
    PREVIEW_PROFILE,
    WorkIQOAuthProfile,
)

logger = logging.getLogger(__name__)

# Every server Precursor can drive a browser sign-in for, in the order we'd
# rather prompt for them (preview first — it's the one that resolves the tenant
# the Agent 365 profiles need).
OAUTH_SERVER_NAMES: Final[tuple[str, ...]] = (
    PREVIEW_PROFILE.server,
    *(spec.name for spec in AGENT365_SERVERS),
)

_LABELS: Final[dict[str, str]] = {
    PREVIEW_PROFILE.server: PREVIEW_PROFILE.label,
    **{spec.name: spec.label for spec in AGENT365_SERVERS},
}

# Static server → credential map. Keyed the same way as
# ``WorkIQOAuthProfile.auth_family`` (the token storage key) so a value from
# here and one read off a live profile are directly comparable.
_CREDENTIALS: Final[dict[str, str]] = {
    PREVIEW_PROFILE.server: PREVIEW_PROFILE.tokens_key,
    **{spec.name: AGENT365_TOKENS_KEY for spec in AGENT365_SERVERS},
}


def is_oauth_server(name: str) -> bool:
    """Whether ``name`` signs in through Precursor's browser OAuth flow.

    Answers the *static* question ("is this one of the OAuth servers at all"),
    not the configuration-dependent one — ``workiq`` is listed here even with
    preview mode off, where it runs as local stdio and cannot sign in. Use
    :func:`profile_for_server` when you need to know whether a sign-in is
    actually possible right now.
    """
    return name in _LABELS


def server_label(name: str) -> str:
    """Human-facing label for ``name``, falling back to the raw server name."""
    return _LABELS.get(name, name)


def credential_key(name: str) -> str:
    """Identity of the credential ``name`` signs in with.

    Servers sharing a key share one token: signing in to either authenticates
    both, so they must raise a single prompt between them. Non-OAuth servers get
    their own name back, which makes this safe to fold over a mixed list without
    accidentally merging unrelated servers.
    """
    return _CREDENTIALS.get(name, name)


def collapse_by_credential(names: list[str]) -> list[str]:
    """Drop servers that duplicate a credential already present in ``names``.

    Order is preserved and the first server seen for a credential wins, so the
    caller keeps whichever one it considered most relevant.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = credential_key(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


async def profile_for_server(name: str) -> WorkIQOAuthProfile | None:
    """Resolve the live OAuth profile for ``name``, or ``None``.

    ``None`` means "this server cannot sign in as things stand" and covers three
    distinct cases the callers all want to treat identically: it isn't an OAuth
    server, it's ``workiq`` with preview mode off (local stdio, no OAuth), or
    it's an Agent 365 server with no tenant resolved yet.
    """
    # Reached through the modules rather than bound at import so the seams stay
    # patchable — several suites swap these out to keep unit tests off the DB.
    if name == PREVIEW_PROFILE.server:
        return PREVIEW_PROFILE if await workiq_preview.resolve_workiq_preview() else None
    if agent365.is_agent365_server(name):
        return await agent365.profile_for(name)
    return None


async def active_profiles() -> list[WorkIQOAuthProfile]:
    """Every OAuth profile that can sign in right now, one entry per server.

    Not deduplicated: callers that filter per server (on the enabled toggle or
    the server's state) need to see both halves of a shared credential before
    deciding. Use :func:`unique_credentials` for "do this once per sign-in".
    """
    names = (PREVIEW_PROFILE.server, *(spec.name for spec in agent365.AGENT365_SERVERS))
    out: list[WorkIQOAuthProfile] = []
    for name in names:
        try:
            profile = await profile_for_server(name)
        except Exception:
            # Tenant resolution touches the DB; a hiccup on one server must not
            # strand the others (notably the preview profile, which needs none).
            logger.debug("OAuth profile resolution failed for %s", name, exc_info=True)
            continue
        if profile is not None:
            out.append(profile)
    return out


async def unique_credentials() -> list[WorkIQOAuthProfile]:
    """:func:`active_profiles` reduced to one profile per credential."""
    seen: set[str] = set()
    out: list[WorkIQOAuthProfile] = []
    for profile in await active_profiles():
        if profile.auth_family in seen:
            continue
        seen.add(profile.auth_family)
        out.append(profile)
    return out
