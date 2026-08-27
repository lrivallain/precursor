"""Update discovery for the running instance.

Precursor is installed in one of two shapes, and each updates differently:

* a **source checkout** (``uv run precursor`` from a clone) — updated with
  ``git pull`` plus a plugin-frontend rebuild, because a working tree carries no
  built plugin UI;
* an **installed wheel** (``uv tool install precursor-ai``) — updated by
  re-installing the wheel, which already bundles the SPA, the docs and every
  plugin frontend.

Both are detected here so the caller (the ``service update`` command and the
tray) never has to ask the user which one they have.

Releases are read from GitHub. Tagged builds follow the ``stable`` channel
(``/releases/latest``); dev builds follow ``nightly``, a rolling prerelease that
carries a small ``version.json`` describing the wheel it just published — one
cheap request instead of parsing asset lists.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import httpx

from precursor import __version__
from precursor.backend.config import get_settings, is_source_checkout

logger = logging.getLogger(__name__)

InstallMode = Literal["source", "uv-tool", "wheel"]
Channel = Literal["stable", "nightly"]

NIGHTLY_TAG = "nightly"
_HTTP_TIMEOUT = 10.0

_cache_lock = threading.Lock()
_cache: tuple[float, UpdateInfo] | None = None


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    current_commit: str | None
    latest_version: str | None
    latest_commit: str | None
    update_available: bool
    channel: Channel
    install_mode: InstallMode
    wheel_url: str | None = None
    # Plugin wheels published alongside the host, so a nightly install gets
    # nightly plugins instead of whatever version is on PyPI.
    extra_wheel_urls: tuple[str, ...] = ()
    release_url: str | None = None
    # Populated when the check itself failed (offline, rate limited, …) so the
    # UI can say "couldn't check" instead of "you're up to date".
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "current_commit": self.current_commit,
            "latest_version": self.latest_version,
            "latest_commit": self.latest_commit,
            "update_available": self.update_available,
            "channel": self.channel,
            "install_mode": self.install_mode,
            "wheel_url": self.wheel_url,
            "extra_wheel_urls": list(self.extra_wheel_urls),
            "release_url": self.release_url,
            "error": self.error,
        }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def install_mode() -> InstallMode:
    if is_source_checkout():
        return "source"
    # `uv tool install` puts each tool in its own venv under the uv tool dir;
    # that is the only shape we can reliably re-install in place.
    prefix = Path(sys.prefix).resolve().as_posix()
    if "/uv/tools/" in prefix or prefix.endswith("/uv/tools"):
        return "uv-tool"
    return "wheel"


def _running_commit() -> str | None:
    """The short sha baked into a hatch-vcs dev version, if this is one."""
    if "+" not in __version__:
        return None
    for part in __version__.split("+", 1)[1].split("."):
        if part.startswith("g") and len(part) > 1:
            return part[1:]
    return None


def default_channel() -> Channel:
    configured = get_settings().update_channel.strip().lower()
    if configured in ("stable", "nightly"):
        return configured  # type: ignore[return-value]
    # An untagged build is by definition ahead of the last release, so comparing
    # it against `stable` would report "up to date" forever.
    return "nightly" if _is_dev_version(__version__) else "stable"


def _is_dev_version(version: str) -> bool:
    return ".dev" in version or "+" in version


_NUM = re.compile(r"\d+")


def _version_key(version: str) -> tuple[int, ...]:
    """Compare CalVer releases without pulling in a version-parsing dependency.

    Only ever used to compare two *tagged* versions (``YYYY.M.MICRO``), where a
    plain numeric tuple is exact. Dev builds are compared by commit sha instead.
    """
    core = version.split("+", 1)[0].split(".dev", 1)[0]
    return tuple(int(match.group()) for match in _NUM.finditer(core))


def _get_json(client: httpx.Client, url: str) -> Any:
    response = client.get(url, timeout=_HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _headers(*, authenticated: bool) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"precursor/{__version__}",
    }
    # The unauthenticated GitHub API allows 60 requests/hour per IP, so reuse a
    # token when the environment already has one. Only for api.github.com: the
    # nightly manifest is a public release asset served from another origin, and
    # sending credentials there buys nothing.
    if authenticated:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _check_nightly(
    client: httpx.Client, repo: str
) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    """Return (version, commit, wheel_url, extra_wheel_urls) for the nightly build."""
    meta = _get_json(
        client,
        f"https://github.com/{repo}/releases/download/{NIGHTLY_TAG}/version.json",
    )
    if not isinstance(meta, dict):
        return None, None, None, ()
    extras = meta.get("extra_wheel_urls")
    return (
        str(meta.get("version") or "") or None,
        str(meta.get("commit") or "") or None,
        str(meta.get("wheel_url") or "") or None,
        tuple(str(url) for url in extras if url) if isinstance(extras, list) else (),
    )


def _check_stable(
    client: httpx.Client, repo: str
) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    release = _get_json(client, f"https://api.github.com/repos/{repo}/releases/latest")
    if not isinstance(release, dict):
        return None, None, None, ()
    tag = str(release.get("tag_name") or "").lstrip("v") or None
    wheel_url: str | None = None
    for asset in release.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.startswith("precursor_ai-") and name.endswith(".whl"):
            wheel_url = str(asset.get("browser_download_url") or "") or None
            break
    # A tagged release resolves its plugins from PyPI, where the matching
    # versions are published by the same workflow.
    return tag, None, wheel_url, ()


def check(*, force: bool = False) -> UpdateInfo:
    """Look up the newest build on the active channel (cached)."""
    global _cache
    cfg = get_settings()
    ttl = float(cfg.update_check_ttl_seconds)
    with _cache_lock:
        cached = _cache
        if not force and cached is not None and (time.monotonic() - cached[0]) < ttl:
            return cached[1]

    channel = default_channel()
    mode = install_mode()
    current_commit = _running_commit()
    base = UpdateInfo(
        current_version=__version__,
        current_commit=current_commit,
        latest_version=None,
        latest_commit=None,
        update_available=False,
        channel=channel,
        install_mode=mode,
        release_url=f"https://github.com/{cfg.update_repo}/releases",
    )

    try:
        with httpx.Client(
            follow_redirects=True, headers=_headers(authenticated=channel == "stable")
        ) as client:
            if channel == "nightly":
                latest_version, latest_commit, wheel_url, extra_wheels = _check_nightly(
                    client, cfg.update_repo
                )
            else:
                latest_version, latest_commit, wheel_url, extra_wheels = _check_stable(
                    client, cfg.update_repo
                )
    except Exception as exc:  # network, rate limit, malformed payload
        logger.debug("Update check failed: %s", exc)
        info = replace(base, error=str(exc))
        with _cache_lock:
            _cache = (time.monotonic(), info)
        return info

    if channel == "nightly":
        # Dev versions are not ordered (two branches can share a base), so the
        # only meaningful question is "is the published build a different
        # commit than the one running?".
        available = bool(latest_commit and current_commit and latest_commit != current_commit)
        # A tagged build asked to follow nightly has no sha to compare against.
        if latest_commit and current_commit is None:
            available = True
    else:
        available = bool(
            latest_version and _version_key(latest_version) > _version_key(__version__)
        )

    info = UpdateInfo(
        current_version=__version__,
        current_commit=current_commit,
        latest_version=latest_version,
        latest_commit=latest_commit,
        update_available=available,
        channel=channel,
        install_mode=mode,
        wheel_url=wheel_url,
        extra_wheel_urls=extra_wheels,
        release_url=base.release_url,
    )
    with _cache_lock:
        _cache = (time.monotonic(), info)
    return info


def invalidate() -> None:
    """Drop the cached result (called right after applying an update)."""
    global _cache
    with _cache_lock:
        _cache = None


class UpdateError(RuntimeError):
    """Applying an update failed."""


def _run(cmd: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if result.returncode != 0:
        raise UpdateError(
            f"`{' '.join(cmd)}` failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[-800:]}"
        )
    return result.stdout


def installed_extras() -> tuple[str, ...]:
    """The extras this tool was actually installed with, per uv's receipt.

    Read from disk rather than from configuration because it is the truth: a
    configured list is a copy the user has to keep in sync by hand, and the
    failure mode is silent. Installing ``precursor-ai[tray,agents]`` and then
    updating against a default of ``kanban`` uninstalls the menu-bar icon and
    Agents mode without saying anything.
    """
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    try:
        with receipt.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    requirements = data.get("tool", {}).get("requirements", [])
    if not isinstance(requirements, list):
        return ()
    for entry in requirements:
        if isinstance(entry, dict) and entry.get("name") == "precursor-ai":
            extras = entry.get("extras") or []
            return tuple(str(e) for e in extras) if isinstance(extras, list) else ()
    return ()


def _requirement() -> str:
    # The receipt wins when there is one; the setting remains the way to add an
    # extra the current install doesn't have yet, and the fallback for installs
    # uv didn't make.
    extras = list(installed_extras())
    for part in get_settings().update_extras.split(","):
        name = part.strip()
        if name and name not in extras:
            extras.append(name)
    return f"precursor-ai[{','.join(extras)}]" if extras else "precursor-ai"


def apply(info: UpdateInfo | None = None) -> str:
    """Upgrade the installation in place. Returns a human-readable summary.

    Does **not** restart anything — the caller owns the process lifecycle (see
    ``precursor.backend.supervisor``).
    """
    info = info or check(force=True)
    mode = info.install_mode

    if mode == "source":
        root = repo_root()
        _run(["git", "pull", "--ff-only"], cwd=root)
        # A checkout ships no built plugin UI, so refresh it; the SPA itself is
        # rebuilt automatically on the next start when it is stale.
        if shutil.which("make"):
            _run(["make", "plugins-build"], cwd=root)
        else:
            logger.warning("make not found — skipping plugin frontend rebuild.")
        invalidate()
        return f"Updated the checkout at {root}."

    if mode != "uv-tool":
        raise UpdateError(
            "This installation was not made with `uv tool install`, so Precursor "
            "cannot upgrade itself safely. Reinstall with "
            f'`uv tool install --force "{_requirement()}"`.'
        )

    uv = shutil.which("uv")
    if uv is None:
        raise UpdateError("`uv` is not on PATH, so the wheel cannot be reinstalled.")

    if info.channel == "nightly":
        if not info.wheel_url:
            raise UpdateError("The nightly release did not advertise a wheel to install.")
        target = f"{_requirement()} @ {info.wheel_url}"
    else:
        target = _requirement()

    cmd = [uv, "tool", "install", "--force", target]
    for extra in info.extra_wheel_urls:
        # Pin the plugin wheels built from the same commit, so a nightly host
        # isn't paired with a months-old plugin resolved from PyPI.
        cmd += ["--with", extra]
    _run(cmd)
    invalidate()
    return f"Installed {info.latest_version or 'the latest build'}."
