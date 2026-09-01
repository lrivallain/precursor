"""What the board lists: extra project sources, minus the ones hidden.

By default the board lists the projects owned by whoever owns the repo in
Settings → GitHub. That is the right default and the wrong ceiling in both
directions:

* A board you care about often belongs to somebody else — a customer's roadmap,
  another org you contribute to — and is invisible from your own account. So the
  plugin keeps a list of extra **sources**, each either an *account* (every open
  board it owns) or a *single project*, written as a GitHub URL or a bare login.
* Conversely an account with thirty boards drowns the picker in boards you never
  open. So the plugin also keeps a list of **hidden** projects, which is applied
  after everything else has been collected.

The two are deliberately different shapes. A source *adds* and can be an account,
so removing one can take several boards with it. Hiding *subtracts* and always
names exactly one board — including boards from the configured repo's owner,
which no source produced and which are therefore otherwise unremovable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from precursor.plugin_api import get_plugin_settings

#: Keys inside the plugin's settings blob.
SOURCES_KEY = "project_sources"
HIDDEN_KEY = "hidden_projects"

#: Guards a slow settings list from turning into a slow board picker.
MAX_SOURCES = 20

#: Hiding costs nothing at list time (it is a set lookup), but the blob is a
#: single JSON document, so it still needs a ceiling.
MAX_HIDDEN = 500

# https://github.com/orgs/<login>/projects/<n>  |  /users/<login>/projects/<n>
_PROJECT_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?:orgs|users)/(?P<owner>[^/]+)/projects/(?P<number>\d+)",
    re.IGNORECASE,
)
# A bare account, optionally as a profile URL.
_OWNER_URL = re.compile(r"^https?://(?:www\.)?github\.com/(?P<owner>[^/?#]+)/?$", re.IGNORECASE)
# GitHub logins: alphanumeric plus hyphens, no leading/trailing hyphen — and
# underscores *inside*, because Enterprise Managed Users are named
# ``<shortcode>_<enterprise>`` (e.g. ``octocat_acme``). Classic github.com
# accounts can't contain one, so a rule written from those alone silently
# rejects every EMU account.
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,37}[A-Za-z0-9])?$")
# ASCII digits only. `str.isdigit()` would be wrong here: it accepts superscripts
# ("²") that `int()` then refuses, turning a typo into an exception.
_DIGITS = re.compile(r"^\d+$")


@dataclass(frozen=True, slots=True)
class ProjectSource:
    """One configured source: an account, or a single project within it."""

    owner: str
    #: Per-owner project number when this pins one board; ``None`` for "all".
    number: int | None = None
    #: The entry exactly as the user typed it. Carried so the UI can delete the
    #: source it came from without re-deriving the string — a project URL and
    #: ``acme#4`` parse to the same source but are different array entries, and
    #: guessing wrong would silently remove nothing. Excluded from equality: it
    #: is provenance, not identity, and two spellings of one source are the same
    #: source.
    raw: str = field(default="", compare=False)

    @property
    def label(self) -> str:
        return f"{self.owner}#{self.number}" if self.number else self.owner


def project_key(owner: str, number: int) -> str:
    """Canonical ``owner#number`` identity for one board.

    Used for the hidden list rather than the ProjectV2 node id: it survives a
    board being re-created, it is legible in the settings blob, and it is the
    same syntax a pinned source already uses.
    """
    return f"{owner.lower()}#{number}"


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
        return ProjectSource(owner=match["owner"], number=int(match["number"]), raw=text)

    match = _OWNER_URL.match(text)
    if match and _LOGIN.match(match["owner"]):
        return ProjectSource(owner=match["owner"], raw=text)

    if "#" in text:
        owner, _, number = text.partition("#")
        owner, number = owner.strip(), number.strip()
        if _LOGIN.match(owner) and _DIGITS.match(number):
            return ProjectSource(owner=owner, number=int(number), raw=text)
        return None

    return ProjectSource(owner=text, raw=text) if _LOGIN.match(text) else None


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


def parse_hidden(values: Any) -> set[str]:
    """Parse the hidden list into canonical ``owner#number`` keys.

    Reuses :func:`parse_source` so a hidden entry accepts the same spellings a
    source does — pasting a project URL works here too. Entries that name a
    whole *account* are dropped rather than hiding everything it owns: hiding is
    per-board by construction, and honouring an accountwide entry would turn a
    deliberately empty-looking picker into an apparently broken one.
    """
    if not isinstance(values, list):
        return set()
    hidden: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            ref = parse_source(value)
        except Exception:  # pragma: no cover - defensive: user-supplied input
            # sitting on the path the whole board depends on.
            ref = None
        if ref is None or ref.number is None:
            continue
        hidden.add(project_key(ref.owner, ref.number))
        if len(hidden) >= MAX_HIDDEN:
            break
    return hidden


@dataclass(frozen=True, slots=True)
class BoardConfig:
    """Everything the listing endpoint needs from the plugin's settings."""

    sources: list[ProjectSource]
    hidden: set[str]


async def board_config(plugin_id: str) -> BoardConfig:
    """Read sources and hidden boards in one pass.

    One settings read rather than two: they live in the same blob, and the
    listing endpoint always wants both.
    """
    settings = await get_plugin_settings(plugin_id)
    return BoardConfig(
        sources=parse_sources(settings.get(SOURCES_KEY)),
        hidden=parse_hidden(settings.get(HIDDEN_KEY)),
    )
