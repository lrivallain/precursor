"""Extra project sources the user has added beyond the configured repo.

By default the board lists the projects owned by whoever owns the repo in
Settings → GitHub. That is the right default and the wrong ceiling: a board you
care about often belongs to somebody else — a customer's roadmap, another org
you contribute to — and is invisible from your own account.

So the plugin keeps a list of extra sources in its own settings. Each entry is
either an **account** (every open board it owns) or a **single project** (just
that one), written as a GitHub URL or a bare login.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from precursor.plugin_api import get_plugin_settings

#: Key inside the plugin's settings blob.
SOURCES_KEY = "project_sources"

#: Guards a slow settings list from turning into a slow board picker.
MAX_SOURCES = 20

# https://github.com/orgs/<login>/projects/<n>  |  /users/<login>/projects/<n>
_PROJECT_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?:orgs|users)/(?P<owner>[^/]+)/projects/(?P<number>\d+)",
    re.IGNORECASE,
)
# A bare account, optionally as a profile URL.
_OWNER_URL = re.compile(r"^https?://(?:www\.)?github\.com/(?P<owner>[^/?#]+)/?$", re.IGNORECASE)
# GitHub logins: alphanumeric plus hyphens, no leading/trailing hyphen.
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
# ASCII digits only. `str.isdigit()` would be wrong here: it accepts superscripts
# ("²") that `int()` then refuses, turning a typo into an exception.
_DIGITS = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class ProjectSource:
    """One configured source: an account, or a single project within it."""

    owner: str
    #: Per-owner project number when this pins one board; ``None`` for "all".
    number: int | None = None

    @property
    def label(self) -> str:
        return f"{self.owner}#{self.number}" if self.number else self.owner


def parse_source(raw: str) -> ProjectSource | None:
    """Interpret a user-typed source, or ``None`` when it isn't one.

    Accepts a project URL, a profile URL, ``owner#number``, or a bare login —
    the forms someone actually has to hand — rather than demanding one syntax.
    """
    text = (raw or "").strip()
    if not text:
        return None

    match = _PROJECT_URL.match(text)
    if match and _LOGIN.match(match["owner"]):
        return ProjectSource(owner=match["owner"], number=int(match["number"]))

    match = _OWNER_URL.match(text)
    if match and _LOGIN.match(match["owner"]):
        return ProjectSource(owner=match["owner"])

    if "#" in text:
        owner, _, number = text.partition("#")
        owner, number = owner.strip(), number.strip()
        if _LOGIN.match(owner) and _DIGITS.match(number):
            return ProjectSource(owner=owner, number=int(number))
        return None

    return ProjectSource(owner=text) if _LOGIN.match(text) else None


def parse_sources(values: Any) -> list[ProjectSource]:
    """Parse the stored list, dropping anything unreadable.

    Unreadable entries are skipped rather than raising: a malformed value should
    cost the user that one source, not the whole board. The cap is applied to
    *valid* entries, so a run of junk can't starve the good ones after it.
    """
    if not isinstance(values, list):
        return []
    out: list[ProjectSource] = []
    seen: set[tuple[str, int | None]] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            source = parse_source(value)
        except Exception:  # pragma: no cover - parse_source is total, but this
            # list is user-supplied and reaches an endpoint the whole board needs.
            source = None
        if source is None:
            continue
        key = (source.owner.lower(), source.number)
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
        if len(out) >= MAX_SOURCES:
            break
    return out


async def configured_sources(plugin_id: str) -> list[ProjectSource]:
    """The extra sources stored in the plugin's settings."""
    settings = await get_plugin_settings(plugin_id)
    return parse_sources(settings.get(SOURCES_KEY))
