"""Where a plugin comes from, which versions it has, and how to ask for one.

A plugin reaches Precursor one of two ways, and each lists and upgrades
differently:

* **PyPI**, by name. ``pypi.org``'s JSON API lists the releases, and an install
  names the distribution — with a floor at the newest release, or pinned to
  the one the user chose.
* **GitHub**, from a repository's releases. Each release has to carry a built
  wheel: a plugin's frontend is a build product, so a source checkout would
  install without its UI. An install names that wheel's URL.

Neither ties a plugin's version to core's. "Latest" means the newest release
the source publishes, and an install that can't deliver it fails rather than
quietly settling for an older one; choosing an older release is the explicit
way around one that doesn't fit this Precursor. An upgrade is simply another
install, so a plugin moves on its own cadence.

Every value that ends up in an installer argument is either validated here
(names, versions, ``owner/repo``) or read from GitHub's own release metadata and
checked to be a GitHub release download, never taken verbatim from a client.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, replace
from importlib import metadata
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from precursor import __version__
from precursor.backend import uv_receipt
from precursor.backend.config import get_settings
from precursor.backend.plugins.catalog import DISTRIBUTION_RE, normalize_distribution

logger = logging.getLogger(__name__)

#: ``direct`` is anything else — a path, an editable checkout, an arbitrary
#: URL — for which there is no list of versions to offer.
SourceKind = Literal["pypi", "github", "direct"]

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
GITHUB_RELEASES_URL = "https://api.github.com/repos/{repo}/releases?per_page=30"
_HTTP_TIMEOUT = 10.0

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}\Z")
#: PEP 440's alphabet, and a tag's. Checked before a version reaches an
#: installer argument, where anything else would change what is being asked for.
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]{0,63}\Z")
_PRERELEASE_RE = re.compile(r"(?:a|b|c|rc|alpha|beta|pre|preview|dev)\d*", re.IGNORECASE)
_RELEASE_ASSET_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/?#]+\.whl)\Z",
    re.IGNORECASE,
)
_NUM = re.compile(r"\d+")


class SourceError(Exception):
    """A lookup or resolution that failed, with the HTTP status it deserves."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Source:
    """How a plugin is (or would be) installed."""

    kind: SourceKind
    #: PEP 503 name, when known — a GitHub source learns it from the wheel.
    distribution: str | None = None
    #: ``owner/repo`` for a GitHub source.
    repository: str | None = None
    #: The constraint the install was asked with (``==1.2``, ``>=1.2``), if any.
    specifier: str = ""

    @property
    def pinned(self) -> bool:
        return self.specifier.startswith("==")

    @property
    def spec(self) -> str:
        """What to hand back to :func:`resolve` to install from the same place."""
        if self.kind == "github" and self.repository:
            return f"https://github.com/{self.repository}"
        return self.distribution or ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "distribution": self.distribution,
            "repository": self.repository,
            "specifier": self.specifier,
            "pinned": self.pinned,
        }


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    prerelease: bool = False
    published_at: str | None = None
    #: The wheel a GitHub release carries. PyPI releases are installed by name.
    wheel_url: str | None = None
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class Releases:
    """Every installable release of one source, newest first."""

    source: Source
    releases: tuple[Release, ...]
    latest: Release | None

    def find(self, version: str) -> Release | None:
        wanted = normalize_version(version)
        for release in self.releases:
            if normalize_version(release.version) == wanted:
                return release
        for release in self.releases:
            if release.tag and normalize_version(release.tag) == wanted:
                return release
        return None


@dataclass(frozen=True, slots=True)
class Resolution:
    """What to hand the installer, and what it is expected to install."""

    requirement: str
    distribution: str | None = None
    version: str | None = None
    #: Where it resolves from; ``None`` for a free-form requirement.
    kind: SourceKind | None = None


# --- recognising a source ---------------------------------------------------


def parse_github_repository(text: str) -> str | None:
    """``owner/repo`` for a GitHub repository link, or ``None`` if it isn't one.

    Takes what people actually paste — with or without the scheme, a trailing
    ``.git``, or a deeper page like ``/releases/tag/v1`` — but only for
    ``github.com`` itself.
    """
    raw = text.strip()
    if not raw:
        return None
    if "://" not in raw:
        if not raw.lower().startswith(("github.com/", "www.github.com/")):
            return None
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or host not in ("github.com", "www.github.com"):
        return None
    if port is not None or parts.username or parts.password:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo) or repo in (".", ".."):
        return None
    return f"{owner}/{repo}"


def github_release_asset(url: str) -> tuple[str, str, str] | None:
    """``(owner/repo, tag, filename)`` for a GitHub release wheel URL."""
    match = _RELEASE_ASSET_RE.match(url.strip())
    if match is None:
        return None
    owner, repo, tag, filename = match.groups()
    return f"{owner}/{repo}", tag, filename


def parse_wheel_filename(filename: str) -> tuple[str, str] | None:
    """``(distribution, version)`` from a standard wheel filename."""
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) not in (5, 6) or not parts[0] or not parts[1]:
        return None
    return normalize_distribution(parts[0]), parts[1]


def classify(spec: str) -> Source | None:
    """The source a user-typed spec names, or ``None`` for a free-form requirement."""
    repository = parse_github_repository(spec)
    if repository is not None:
        return Source("github", repository=repository)
    name = spec.strip()
    if DISTRIBUTION_RE.match(name):
        return Source("pypi", distribution=normalize_distribution(name))
    return None


def _from_url(distribution: str, url: str) -> Source:
    asset = github_release_asset(url)
    if asset is not None:
        return Source("github", distribution, repository=asset[0])
    return Source("direct", distribution)


def _from_direct_url(distribution: str) -> Source | None:
    """Read PEP 610's ``direct_url.json``, which every URL or path install records."""
    try:
        raw = metadata.distribution(distribution).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str):
        return None
    if isinstance(data.get("vcs_info"), dict):
        repository = parse_github_repository(url)
        if repository is None:
            return Source("direct", distribution)
        return Source("github", distribution, repository=repository)
    return _from_url(distribution, url)


def installed_source(distribution: str) -> Source:
    """How an installed plugin got here, which decides where its upgrades come from.

    uv's receipt comes first because it is what the next rebuild restates;
    ``direct_url.json`` covers every other installer. With neither, the plugin
    came from an index by name.
    """
    name = normalize_distribution(distribution)
    for requirement in uv_receipt.siblings():
        if normalize_distribution(requirement.name) != name:
            continue
        if requirement.url:
            return _from_url(name, requirement.url)
        return Source("pypi", name, specifier=requirement.specifier)
    return _from_direct_url(name) or Source("pypi", name)


# --- versions ---------------------------------------------------------------


def normalize_version(version: str) -> str:
    value = version.strip().lower()
    return value[1:] if value.startswith("v") and value[1:2].isdigit() else value


def _is_prerelease(version: str) -> bool:
    # The local part (`+g1a2b3c`) is a build label, not a pre-release marker.
    return bool(_PRERELEASE_RE.search(version.split("+", 1)[0]))


def _numeric_key(version: str) -> tuple[int, ...]:
    core = normalize_version(version).split("+", 1)[0]
    return tuple(int(match.group()) for match in _NUM.finditer(core))


def _default_latest(releases: list[Release]) -> Release | None:
    return next((r for r in releases if not r.prerelease), releases[0] if releases else None)


def _newest_first(releases: list[Release]) -> list[Release]:
    return sorted(
        releases, key=lambda r: (r.published_at or "", _numeric_key(r.version)), reverse=True
    )


def is_newer(releases: Releases, installed: str | None) -> bool:
    """Whether ``releases.latest`` is ahead of the ``installed`` version.

    Ordered by the source's own release order rather than by parsing versions:
    that is exact for anything the source published, and a local build it never
    heard of falls back to comparing the numbers.
    """
    latest = releases.latest
    if latest is None or not installed:
        return False
    if normalize_version(latest.version) == normalize_version(installed):
        return False
    order = {normalize_version(r.version): i for i, r in enumerate(releases.releases)}
    here = order.get(normalize_version(installed))
    if here is None:
        return _numeric_key(latest.version) > _numeric_key(installed)
    return order[normalize_version(latest.version)] < here


def _pypi_releases(source: Source, data: Any) -> Releases:
    if not isinstance(data, dict):
        raise SourceError("PyPI answered with something that isn't a project.")
    releases: list[Release] = []
    for version, files in (data.get("releases") or {}).items():
        if not isinstance(files, list):
            continue
        # A release whose every file was yanked is not one to offer.
        live = [f for f in files if isinstance(f, dict) and not f.get("yanked")]
        if not live:
            continue
        uploaded = min(
            (str(f.get("upload_time_iso_8601") or f.get("upload_time") or "") for f in live),
            default="",
        )
        releases.append(
            Release(
                version=str(version),
                prerelease=_is_prerelease(str(version)),
                published_at=uploaded or None,
            )
        )
    ordered = _newest_first(releases)
    info = data.get("info")
    if not isinstance(info, dict):
        info = {}
    current = str(info.get("version") or "")
    latest = next((r for r in ordered if r.version == current), None) or _default_latest(ordered)
    name = normalize_distribution(str(info.get("name") or source.distribution or ""))
    return Releases(replace(source, distribution=name), tuple(ordered), latest)


def _github_releases(source: Source, payload: Any) -> Releases:
    if not isinstance(payload, list):
        raise SourceError("GitHub answered with something that isn't a release list.")
    repository = source.repository or ""
    candidates: list[tuple[dict[str, Any], list[tuple[str, str, str, str]]]] = []
    for release in payload:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        wheels: list[tuple[str, str, str, str]] = []
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            filename = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            parsed = parse_wheel_filename(filename)
            # Only a GitHub release download ever reaches the installer.
            if parsed is not None and github_release_asset(url) is not None:
                wheels.append((parsed[0], parsed[1], url, filename))
        if wheels:
            candidates.append((release, wheels))
    if not candidates:
        raise SourceError(f"No release of github.com/{repository} carries a wheel to install.", 404)

    distribution = source.distribution or _pick_distribution(repository, candidates[0][1])
    releases: list[Release] = []
    for release, wheels in candidates:
        mine = [w for w in wheels if w[0] == distribution]
        if not mine:
            continue
        # A pure-Python wheel installs everywhere; prefer it over a tagged one.
        best = next((w for w in mine if w[3].endswith("-py3-none-any.whl")), mine[0])
        releases.append(
            Release(
                version=best[1],
                prerelease=bool(release.get("prerelease")) or _is_prerelease(best[1]),
                published_at=str(release.get("published_at") or release.get("created_at") or "")
                or None,
                wheel_url=best[2],
                tag=str(release.get("tag_name") or "") or None,
            )
        )
    if not releases:
        raise SourceError(
            f"No release of github.com/{repository} carries a wheel for {distribution}.", 404
        )
    ordered = _newest_first(releases)
    return Releases(
        replace(source, distribution=distribution), tuple(ordered), _default_latest(ordered)
    )


def _pick_distribution(repository: str, wheels: list[tuple[str, str, str, str]]) -> str:
    """The distribution a repository publishes, when its releases carry several."""
    names = [w[0] for w in wheels]
    by_repo = normalize_distribution(repository.rsplit("/", 1)[-1])
    return by_repo if by_repo in names else names[0]


_cache: dict[tuple[str, str, str], tuple[float, Releases]] = {}


def invalidate() -> None:
    _cache.clear()


def _headers() -> dict[str, str]:
    return {"User-Agent": f"precursor/{__version__}"}


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    try:
        return await client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise SourceError(f"Couldn't reach {urlsplit(url).hostname}: {exc}") from exc


async def _fetch_pypi(client: httpx.AsyncClient, source: Source) -> Releases:
    response = await _get_json(client, PYPI_JSON_URL.format(name=source.distribution))
    if response.status_code == 404:
        raise SourceError(f"{source.distribution} isn't on PyPI.", 404)
    if response.is_error:
        raise SourceError(f"PyPI answered {response.status_code} for {source.distribution}.")
    return _pypi_releases(source, response.json())


async def _fetch_github(client: httpx.AsyncClient, source: Source, token: str | None) -> Releases:
    url = GITHUB_RELEASES_URL.format(repo=source.repository)
    base = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    response = await _get_json(
        client, url, headers={**base, "Authorization": f"Bearer {token}"} if token else base
    )
    if response.status_code == 401 and token:
        # A stale token must not hide a public repository.
        response = await _get_json(client, url, headers=base)
    if response.status_code == 404:
        raise SourceError(f"github.com/{source.repository} doesn't exist or isn't visible.", 404)
    if response.status_code in (403, 429):
        raise SourceError(
            "GitHub refused the request, most likely its rate limit for anonymous "
            "callers. Connect a GitHub account under Settings → GitHub and retry."
        )
    if response.is_error:
        raise SourceError(f"GitHub answered {response.status_code} for {source.repository}.")
    return _github_releases(source, response.json())


async def list_releases(
    source: Source, *, token: str | None = None, refresh: bool = False
) -> Releases:
    """Every installable release of ``source``, newest first (cached per source)."""
    if source.kind == "pypi" and source.distribution:
        key = ("pypi", source.distribution, "")
    elif source.kind == "github" and source.repository:
        key = ("github", source.repository.lower(), source.distribution or "")
    else:
        raise SourceError("Precursor can't list versions for this plugin's source.", 400)

    ttl = float(get_settings().update_check_ttl_seconds)
    cached = _cache.get(key)
    if not refresh and cached is not None and (time.monotonic() - cached[0]) < ttl:
        return cached[1]

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_headers()
    ) as client:
        if source.kind == "pypi":
            result = await _fetch_pypi(client, source)
        else:
            result = await _fetch_github(client, source, token)
    _cache[key] = (time.monotonic(), result)
    return result


# --- turning a choice into a requirement ------------------------------------


def requirement_for(releases: Releases, release: Release | None) -> str:
    """The requirement installing ``release`` (``None`` = the newest) from ``releases``.

    From PyPI, "the newest" is a floor rather than a bare name. A bare name
    lets the resolver fall back to whatever the index serves — an index that
    lags PyPI then installs an older release than the one the user was shown,
    and pulls in whatever that release depends on. A floor fails loudly
    instead, and unlike an ``==`` pin it doesn't stop the next upgrade. A
    GitHub release is only reachable by its wheel's URL, so there it is always
    that one release.
    """
    source = releases.source
    name = source.distribution or ""
    if source.kind == "github":
        target = release or releases.latest
        if target is None or not target.wheel_url:
            raise SourceError(f"github.com/{source.repository} has no wheel to install.", 404)
        return f"{name} @ {target.wheel_url}"
    if release is not None:
        return f"{name}=={release.version}"
    if releases.latest is not None:
        return f"{name}>={releases.latest.version}"
    return name


async def resolve(
    spec: str,
    version: str | None = None,
    *,
    token: str | None = None,
    distribution: str | None = None,
) -> Resolution:
    """Turn what the user asked for into the requirement to install.

    ``spec`` is a package name, a GitHub repository link, or any other
    requirement the installer accepts (passed through untouched, and only
    without a ``version``). ``distribution`` narrows a GitHub repository that
    publishes several.
    """
    chosen = (version or "").strip() or None
    if chosen is not None and not VERSION_RE.match(chosen):
        raise SourceError(f"{chosen!r} isn't a version.", 400)

    source = classify(spec)
    if source is None:
        if chosen is not None:
            raise SourceError(
                "A version can only be chosen for a package name or a GitHub repository.", 400
            )
        return Resolution(requirement=spec.strip())
    if distribution:
        source = replace(source, distribution=normalize_distribution(distribution))

    if source.kind == "github":
        releases = await list_releases(source, token=token)
        release = releases.find(chosen) if chosen else releases.latest
        if release is None:
            raise SourceError(f"github.com/{source.repository} has no release {chosen}.", 404)
        return Resolution(
            requirement_for(releases, release),
            releases.source.distribution,
            release.version,
            kind="github",
        )

    name = source.distribution or ""
    if chosen is not None:
        return Resolution(f"{name}=={chosen}", name, chosen, kind="pypi")
    try:
        releases = await list_releases(source)
    except SourceError as exc:
        # Couldn't learn the newest version (offline, a private index PyPI has
        # never heard of): the bare name still installs what the index serves,
        # which beats refusing outright.
        logger.info("Installing %s without a floor: %s", name, exc)
        return Resolution(name, name, kind="pypi")
    latest = releases.latest.version if releases.latest else None
    return Resolution(requirement_for(releases, None), name, latest, kind="pypi")
