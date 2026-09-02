"""The plugin catalog: a curated, *bundled* directory of installable plugins.

Precursor ships the catalogue inside its own distribution rather than fetching
it at runtime. That is a deliberate trade:

* it works offline, adds no network failure states, and never phones home;
* an entry is only ever added by a reviewed pull request, so the list a user
  sees is one a human approved.

The cost is that a newly submitted plugin appears in the next Precursor release,
which for a curated list of a handful of entries is the right side of the trade.

**One file is one plugin.** Each entry is a markdown page under
``website/plugins/``: its YAML frontmatter is the metadata, its body is the
documentation published at ``/plugins/<id>`` on the site and bundled into the
app's own ``/docs``. Submitting a plugin is therefore adding a single file — see
``website/plugins/submitting.md``.

Security note
-------------
An entry's ``distribution`` is fed to an installer, so it is validated to be a
**bare PyPI project name** — :data:`DISTRIBUTION_RE` rejects ``@``, URLs, paths,
extras, version specifiers and markers. Without that, a merged pull request
could turn ``pkg @ https://example.invalid/evil.whl`` into code execution on
every machine that opens the catalogue. Entries are validated here at load time
*and* in the test suite, so a malformed one fails CI before it can ship.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

#: A bare PyPI project name, per PEP 508's ``name`` production. Deliberately the
#: *whole* of what a catalog entry may ask to install: anything expressing a
#: location (``@ url``, a path), an extra or a version is refused outright.
#:
#: ``\Z`` rather than ``$``: Python's ``$`` also matches *before* a trailing
#: newline, so ``"pkg\n"`` would pass. :func:`parse_entry` strips first and would
#: not notice, but a security check must not depend on its caller having
#: sanitised the input.
DISTRIBUTION_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?\Z")

#: A plugin id — the entry-point name, which also namespaces everything the
#: plugin registers and can appear in a URL. ``\Z`` for the same reason as above.
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\Z")

#: What a plugin may declare that it contributes. Kept closed so the UI can
#: render each one, and so a typo is a CI failure rather than a silent no-op.
CONTRIBUTIONS = frozenset({"section", "settings", "mcp", "api"})

#: Frontmatter delimiter for a markdown catalog entry.
_FENCE = "---"

#: Directory inside the installed package that carries the bundled entries.
#: ``hatch_build.py`` relocates :data:`SOURCE_DIR` here when it builds the wheel;
#: the two must agree or an installed Precursor silently shows an empty
#: catalogue, so ``tests/test_plugin_catalog.py`` pins them together.
WHEEL_DIR = "catalog"

#: Where entries are authored, relative to the repository root. They live under
#: ``website/`` because each entry *is* its published documentation page.
SOURCE_DIR = ("website", "plugins")


class CatalogError(ValueError):
    """A catalog entry that can't be trusted or understood."""


@dataclass(slots=True, frozen=True)
class CatalogEntry:
    """One plugin offered in the catalogue."""

    #: Entry-point name. Matches ``LoadedPlugin.id`` once the plugin is installed.
    id: str
    #: PyPI project name — the only thing ever handed to an installer.
    distribution: str
    title: str
    summary: str
    homepage: str | None = None
    author: str | None = None
    license: str | None = None
    tags: tuple[str, ...] = ()
    contributes: tuple[str, ...] = ()
    #: Featured entries lead the list; the rest are merely listed.
    recommended: bool = False
    #: In-app path to this entry's own documentation page, which ships in the
    #: bundled docs site. Absolute so the SPA can link it without knowing the
    #: catalogue's layout.
    docs_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["contributes"] = list(self.contributes)
        return data


@dataclass(slots=True)
class _Raw:
    slug: str
    frontmatter: dict[str, Any] = field(default_factory=dict)


def catalog_dir() -> Path | None:
    """Locate the bundled catalog entries.

    Mirrors how the SPA and docs bundles are found in ``backend/main.py``: an
    installed wheel carries them at ``precursor/catalog`` (see
    ``hatch_build.py``), while a source checkout falls back to the authoring
    location so a plain ``uvicorn`` run from a clone behaves identically.
    """
    try:
        resource = files("precursor").joinpath(WHEEL_DIR)
        with as_file(resource) as path:
            if path.is_dir():
                return path
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root.joinpath(*SOURCE_DIR)
    return candidate if candidate.is_dir() else None


def split_frontmatter(text: str) -> dict[str, Any] | None:
    """Return a markdown file's YAML frontmatter, or ``None`` when it has none.

    Only the leading ``---`` fenced block counts, which is what every markdown
    toolchain (VitePress included) considers frontmatter — so the app and the
    docs site read exactly the same bytes the same way.
    """
    if not text.startswith(_FENCE):
        return None
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            block = "\n".join(lines[1:index])
            break
    else:
        return None
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid YAML frontmatter: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def _require_str(data: dict[str, Any], key: str, slug: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{slug}: '{key}' is required and must be a non-empty string")
    return value.strip()


def _optional_str(data: dict[str, Any], key: str, slug: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{slug}: '{key}' must be a non-empty string when present")
    return value.strip()


def _string_list(data: dict[str, Any], key: str, slug: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise CatalogError(f"{slug}: '{key}' must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CatalogError(f"{slug}: '{key}' must contain only non-empty strings")
        out.append(item.strip())
    return tuple(out)


def parse_entry(slug: str, frontmatter: dict[str, Any]) -> CatalogEntry:
    """Validate one entry's frontmatter into a :class:`CatalogEntry`.

    Raises :class:`CatalogError` rather than returning a partially-trusted
    entry: everything here either ends up in the UI or is handed to an
    installer, so "nearly valid" is not a state worth carrying forward.
    """
    plugin_id = _require_str(frontmatter, "plugin", slug)
    if not PLUGIN_ID_RE.match(plugin_id):
        raise CatalogError(
            f"{slug}: 'plugin' must be lowercase alphanumeric with dashes, got {plugin_id!r}"
        )
    if plugin_id != slug:
        # The filename is the documentation URL and the id is what the app
        # matches against installed plugins; letting them drift means the
        # catalogue links one plugin to another's page.
        raise CatalogError(f"{slug}: 'plugin' must match the file name ({plugin_id!r} != {slug!r})")

    distribution = _require_str(frontmatter, "distribution", slug)
    if not DISTRIBUTION_RE.match(distribution):
        raise CatalogError(
            f"{slug}: 'distribution' must be a bare PyPI project name (no URL, "
            f"extra or version specifier), got {distribution!r}"
        )

    homepage = _optional_str(frontmatter, "homepage", slug)
    if homepage is not None and not homepage.startswith("https://"):
        raise CatalogError(f"{slug}: 'homepage' must be an https:// URL, got {homepage!r}")

    contributes = _string_list(frontmatter, "contributes", slug)
    unknown = set(contributes) - CONTRIBUTIONS
    if unknown:
        raise CatalogError(
            f"{slug}: unknown 'contributes' values {sorted(unknown)}; "
            f"expected any of {sorted(CONTRIBUTIONS)}"
        )

    recommended = frontmatter.get("recommended", False)
    if not isinstance(recommended, bool):
        raise CatalogError(f"{slug}: 'recommended' must be true or false")

    return CatalogEntry(
        id=plugin_id,
        distribution=distribution,
        title=_require_str(frontmatter, "title", slug),
        summary=_require_str(frontmatter, "description", slug),
        homepage=homepage,
        author=_optional_str(frontmatter, "author", slug),
        license=_optional_str(frontmatter, "license", slug),
        tags=_string_list(frontmatter, "tags", slug),
        contributes=contributes,
        recommended=recommended,
        docs_path=f"/docs/plugins/{plugin_id}",
    )


def read_raw_entries(directory: Path) -> list[_Raw]:
    """Every markdown file in ``directory`` that declares itself an entry.

    A page is a catalog entry **iff** its frontmatter carries ``distribution``,
    which is what lets the catalogue's own index and submission guide live
    alongside the entries as ordinary pages.
    """
    out: list[_Raw] = []
    for path in sorted(directory.glob("*.md")):
        try:
            frontmatter = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, CatalogError) as exc:
            logger.warning("Skipping catalog file %s: %s", path.name, exc)
            continue
        if not frontmatter or "distribution" not in frontmatter:
            continue
        out.append(_Raw(slug=path.stem, frontmatter=frontmatter))
    return out


def _sort_key(entry: CatalogEntry) -> tuple[int, str]:
    # Recommended first, then alphabetical — a stable order the UI can render
    # as-is, so "featured" isn't re-derived on the client.
    return (0 if entry.recommended else 1, entry.title.lower())


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogEntry, ...]:
    """Every valid catalog entry, ordered for display.

    An invalid entry is logged and skipped rather than raising: the catalogue is
    a convenience, and one bad file should never take the Plugins panel — or the
    app — down with it. ``tests/test_plugin_catalog.py`` runs the same
    validation strictly, so a malformed entry fails CI long before it ships.
    """
    directory = catalog_dir()
    if directory is None:
        logger.debug("No plugin catalog directory found; the catalogue will be empty.")
        return ()
    entries: list[CatalogEntry] = []
    seen: dict[str, str] = {}
    for raw in read_raw_entries(directory):
        try:
            entry = parse_entry(raw.slug, raw.frontmatter)
        except CatalogError as exc:
            logger.warning("Ignoring invalid plugin catalog entry: %s", exc)
            continue
        previous = seen.get(entry.distribution.lower())
        if previous is not None:
            logger.warning(
                "Ignoring catalog entry %r: %r already offers %s",
                entry.id,
                previous,
                entry.distribution,
            )
            continue
        seen[entry.distribution.lower()] = entry.id
        entries.append(entry)
    return tuple(sorted(entries, key=_sort_key))


def normalize_distribution(name: str) -> str:
    """PEP 503 normalisation, so ``Precursor_Kanban`` matches ``precursor-kanban``."""
    return re.sub(r"[-_.]+", "-", name).lower()
