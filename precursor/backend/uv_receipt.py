"""Reading uv's tool-install receipt.

``uv tool install`` writes ``uv-receipt.toml`` next to the environment,
recording the requirements the tool was *requested* with. That file matters far
more than it looks, because a uv tool environment is **rebuilt from those
requirements** every time anything reinstalls it: whatever is not named there is
gone afterwards, silently.

Two things therefore live in the receipt and nowhere else:

* the **extras** the host carries (``precursor-ai[tray]``), and the pinned wheel
  URL a nightly install was made from;
* the **sibling distributions** added with ``--with`` — which is how a plugin
  that ships from its own repository persists. Core's ``kanban`` extra is a
  convenience, not the mechanism; an out-of-tree plugin has no extra to be named
  by and depends entirely on being read back from here.

So anything that rebuilds the environment (``services/updates``) or extends it
(``plugins/install``) has to start here, or it reinstalls something narrower
than what the user had.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

#: The distribution that owns a Precursor tool environment.
HOST = "precursor-ai"

_DIST_NAME = re.compile(r"^[A-Za-z0-9._-]+")
# A wheel URL ends in the standard `{name}-{version}-…​.whl` filename, so the
# distribution is everything before the first hyphen followed by a digit.
_WHEEL_URL = re.compile(r"/([A-Za-z0-9._]+?)-\d[^/]*\.whl$")


def canonical_name(value: str) -> str:
    """The PEP 503 name a requirement or wheel URL installs.

    Used to decide whether two ``--with`` arguments mean the same distribution,
    where one may be a bare name and the other a pinned wheel URL.
    """
    if (wheel := _WHEEL_URL.search(value)) is not None:
        return wheel.group(1).replace("_", "-").lower()
    head = value.split("@", 1)[0].strip() if " @ " in value else value.strip()
    match = _DIST_NAME.match(head)
    return match.group().replace("_", "-").lower() if match else head.lower()


@dataclass(frozen=True)
class Requirement:
    """One entry of the receipt's ``requirements`` list."""

    name: str
    extras: tuple[str, ...] = ()
    specifier: str = ""
    url: str = ""

    def as_argument(self) -> str:
        """The requirement as uv accepts it back on the command line."""
        base = f"{self.name}[{','.join(self.extras)}]" if self.extras else self.name
        return f"{base} @ {self.url}" if self.url else f"{base}{self.specifier}"


def _path(prefix: str | None = None) -> Path:
    return Path(prefix or sys.prefix) / "uv-receipt.toml"


def requirements(prefix: str | None = None) -> tuple[Requirement, ...]:
    """Every requirement in the receipt, or ``()`` when there isn't a usable one.

    A missing or corrupt receipt is not an error: a source checkout has none,
    and a half-written one must not take an update or a settings page down.
    """
    try:
        with _path(prefix).open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    entries = data.get("tool", {}).get("requirements", [])
    if not isinstance(entries, list):
        return ()

    parsed: list[Requirement] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        extras = entry.get("extras")
        parsed.append(
            Requirement(
                name=name,
                extras=tuple(str(e) for e in extras) if isinstance(extras, list) else (),
                specifier=str(entry.get("specifier") or ""),
                url=str(entry.get("url") or ""),
            )
        )
    return tuple(parsed)


def host(prefix: str | None = None) -> Requirement | None:
    """The ``precursor-ai`` requirement itself, extras and wheel URL included."""
    for requirement in requirements(prefix):
        if requirement.name == HOST:
            return requirement
    return None


def siblings(prefix: str | None = None) -> tuple[Requirement, ...]:
    """Distributions installed alongside the host with ``--with``.

    In practice these are the plugins: a distribution that lives outside this
    repository can only survive a reinstall by being named again, and this is
    the only record that it was ever asked for.
    """
    return tuple(r for r in requirements(prefix) if r.name != HOST)
