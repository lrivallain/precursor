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

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import httpx

from precursor import __version__
from precursor.backend import uv_receipt, winproc
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
    # Wheels published alongside the host on the same nightly release, installed
    # with it so a nightly gets same-commit companions instead of whatever is on
    # PyPI. Normally empty: plugins release from their own repositories.
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


@dataclass(frozen=True)
class NightlyBuild:
    """What the rolling nightly release carries right now."""

    version: str | None
    wheel_url: str
    extra_wheel_urls: tuple[str, ...] = ()


_nightly_cache: tuple[float, NightlyBuild | None] | None = None


def is_nightly_asset(url: str) -> bool:
    """Whether ``url`` is a file on the rolling nightly release.

    Such a URL only lives until the next push to main: the workflow deletes the
    release and re-creates it with the new build's files, so a uv receipt
    pinned to one ends up naming a 404.
    """
    prefix = f"https://github.com/{get_settings().update_repo}/releases/download/{NIGHTLY_TAG}/"
    return bool(url) and url.startswith(prefix)


def published_nightly(*, force: bool = False) -> NightlyBuild | None:
    """The build the nightly release carries now; ``None`` when it can't be read.

    Cached like :func:`check`, failures included, so an offline instance pays
    for the lookup once per TTL rather than on every call.
    """
    global _nightly_cache
    cfg = get_settings()
    ttl = float(cfg.update_check_ttl_seconds)
    with _cache_lock:
        cached = _nightly_cache
        if not force and cached is not None and (time.monotonic() - cached[0]) < ttl:
            return cached[1]

    build: NightlyBuild | None = None
    try:
        with httpx.Client(follow_redirects=True, headers=_headers(authenticated=False)) as client:
            version, _commit, wheel_url, extras = _check_nightly(client, cfg.update_repo)
        if wheel_url:
            build = NightlyBuild(version=version, wheel_url=wheel_url, extra_wheel_urls=extras)
    except Exception as exc:  # network, malformed payload
        logger.debug("Nightly manifest lookup failed: %s", exc)
    with _cache_lock:
        _nightly_cache = (time.monotonic(), build)
    return build


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
    global _cache, _nightly_cache
    with _cache_lock:
        _cache = None
        _nightly_cache = None


class UpdateError(RuntimeError):
    """Applying an update failed."""


def _run(cmd: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        # uv writes UTF-8 (box-drawing and all) whatever the Windows code page
        # says, and the locale default would garble it — or fail outright.
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=900,
        **winproc.no_window(),
    )
    if result.returncode != 0:
        # Reason first, command last: this ends up in a tray notification, which
        # truncates — and the command is the half that explains nothing. The
        # install line carries a full wheel URL, so leading with it used to eat
        # the whole toast and leave "updating failed" as the only signal.
        raise UpdateError(
            f"{_detail(result.stderr or result.stdout) or 'no output'} "
            f"(`{_describe(cmd)}` exited {result.returncode})"
        )
    return result.stdout


# uv reports resolution failures as a box-drawing tree wrapped at ~80 columns,
# which reads as noise once it reaches a notification or a toast. The ranges are
# the multiplication sign uv heads an error with, plus Box Drawing and Geometric
# Shapes — the glyphs it draws the tree itself with.
_UV_BOX = re.compile(r"^[\s\u00d7\u2500-\u257f\u25a0-\u25ff]+")
_URL = re.compile(r"https?://\S*/")


def _detail(text: str) -> str:
    """Flatten a command's error output into a single readable sentence."""
    lines = (_UV_BOX.sub("", line).strip() for line in text.strip().splitlines())
    detail = " ".join(line for line in lines if line)
    return f"{detail[:600]}…" if len(detail) > 600 else detail


def _describe(cmd: Sequence[str]) -> str:
    """The command with URLs reduced to their filename, so it stays readable."""
    return " ".join(_URL.sub("…/", arg) for arg in cmd)


def installed_extras() -> tuple[str, ...]:
    """The extras this tool was actually installed with, per uv's receipt.

    Read from disk rather than from configuration because it is the truth: a
    configured list is a copy the user has to keep in sync by hand, and the
    failure mode is silent. Installing ``precursor-ai[tray,agents]`` and then
    updating against a default of ``kanban`` uninstalls the menu-bar icon and
    Agents mode without saying anything.
    """
    installed = uv_receipt.host()
    return installed.extras if installed else ()


def installed_plugins() -> tuple[str, ...]:
    """Distributions installed *alongside* the host, as uv command arguments.

    These are the out-of-tree plugins. They have no extra in core's metadata to
    be named by — that is the point of shipping from their own repository — so
    the receipt is the only record that they were asked for. Reinstalling
    without re-stating them uninstalls every one of them, which is what
    ``precursor service update`` used to do on every single run.
    """
    return tuple(r.as_argument() for r in uv_receipt.siblings())


def _extras() -> tuple[str, ...]:
    """The extras to reinstall with.

    The receipt wins when there is one; the setting remains the way to add an
    extra the current install doesn't have yet, and the fallback for installs uv
    didn't make. A ``-name`` entry *removes* one — without it there is no
    supported way to stop asking for an extra the local index cannot serve, and
    reinstalling by hand is the only escape.
    """
    extras = list(installed_extras())
    for part in get_settings().update_extras.split(","):
        name = part.strip()
        if name.startswith("-"):
            dropped = name[1:].strip()
            if dropped in extras:
                extras.remove(dropped)
        elif name and name not in extras:
            extras.append(name)
    return tuple(extras)


def _requirement(extras: Sequence[str] | None = None) -> str:
    names = _extras() if extras is None else tuple(extras)
    return f"precursor-ai[{','.join(names)}]" if names else "precursor-ai"


# A Precursor plugin ships as its own distribution, named `precursor-<plugin>`.
# That naming is what tells an "optional plugin" extra apart from one that only
# pulls third-party libraries the host itself uses.
_PLUGIN_DIST = "precursor-"
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")
_DIST_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def _extra_requirements() -> dict[str, tuple[str, ...]]:
    """Map each extra of the running distribution to the distributions it pulls."""
    try:
        requires = metadata.metadata("precursor-ai").get_all("Requires-Dist") or []
    except metadata.PackageNotFoundError:
        return {}
    mapping: dict[str, list[str]] = {}
    for raw in requires:
        requirement, _, marker = str(raw).partition(";")
        extra = _EXTRA_MARKER.search(marker)
        dist = _DIST_NAME.match(requirement.strip())
        if extra and dist:
            mapping.setdefault(extra.group(1), []).append(dist.group().replace("_", "-").lower())
    return {extra: tuple(dists) for extra, dists in mapping.items()}


def plugin_extras(extras: Sequence[str]) -> tuple[str, ...]:
    """The subset of ``extras`` that only pull a separate Precursor plugin.

    Those are optional by construction — the host runs fine without them — so an
    index that cannot serve one must not be able to take the whole update down
    with it.
    """
    requirements = _extra_requirements()
    return tuple(
        extra
        for extra in extras
        if (dists := requirements.get(extra)) and all(d.startswith(_PLUGIN_DIST) for d in dists)
    )


def _install_cmd(
    uv: str, info: UpdateInfo, extras: Sequence[str], with_arguments: Sequence[str]
) -> list[str]:
    requirement = _requirement(extras)
    target = f"{requirement} @ {info.wheel_url}" if info.channel == "nightly" else requirement
    cmd = [uv, "tool", "install", "--force", target]
    for argument in with_arguments:
        cmd += ["--with", argument]
    return cmd


def _with_arguments(info: UpdateInfo) -> tuple[str, ...]:
    """Everything to re-state with ``--with``, same-commit pins winning.

    The receipt carries the plugins this install actually has; the release
    manifest carries wheels for the ones built alongside this host. When both
    name a distribution the manifest wins — pairing a nightly host with a plugin
    resolved from PyPI is exactly what pinning them together avoids.
    """
    arguments: dict[str, str] = {}
    for argument in installed_plugins():
        arguments[uv_receipt.canonical_name(argument)] = argument
    for url in info.extra_wheel_urls:
        arguments[uv_receipt.canonical_name(url)] = url
    return tuple(arguments.values())


def applies_out_of_process(info: UpdateInfo) -> bool:
    """Whether :func:`apply` hands the install to a helper and returns early.

    True on Windows for a ``uv tool`` install, where the environment can't be
    replaced while anything — this process included — runs from it. The caller
    then owns nothing further: the helper stops, installs and restarts.
    """
    return os.name == "nt" and info.install_mode == "uv-tool"


#: Written by the Windows updater (see ``precursor/backend/windows_updater.py``)
#: once the install finishes, and reported by the restarted tray.
WINDOWS_UPDATE_RESULT = "update-result.json"
WINDOWS_UPDATE_LOG = "update.log"


def _base_interpreter() -> str:
    """An interpreter outside the tool environment the update replaces.

    A venv's ``python.exe`` is only a launcher for its base interpreter, which
    ``sys._base_executable`` names. The console one, even from ``pythonw``, so
    the helper's children inherit its hidden console instead of opening their
    own.
    """
    base = Path(getattr(sys, "_base_executable", "") or sys.executable)
    console = base.with_name("python.exe")
    return str(console if console.is_file() else base)


def _apply_out_of_process(
    info: UpdateInfo, commands: list[list[str]], summaries: list[str]
) -> str:  # pragma: no cover - Windows-only path
    """Stop what holds the environment, and let a helper finish the update.

    The app is stopped here, gracefully, while this process can still do it
    properly; the tray (unless it is the caller) is stopped too, and both are
    started again by the helper once the new build is in place.
    """
    from precursor.backend import supervisor, tray
    from precursor.backend.autostart import TRAY, windows_command

    settings = get_settings()
    logs = Path(settings.logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    data = Path(settings.data_dir)
    result = data / WINDOWS_UPDATE_RESULT
    with contextlib.suppress(OSError):
        result.unlink()

    was_running = supervisor.status().running
    if was_running:
        supervisor.stop()
    caller_is_tray = tray.is_current_process()
    tray_was_running = caller_is_tray or tray.stop_running()

    helper = logs / "windows-updater.py"
    helper.write_text(
        (Path(__file__).resolve().parents[1] / "windows_updater.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    job = {
        "wait_pids": [os.getpid()],
        "commands": commands,
        "summaries": summaries,
        # The console interpreter, under the helper's hidden console: its
        # output is what the update log records about the restart.
        "app_command": (
            [
                str(Path(sys.executable).with_name("python.exe")),
                "-m",
                "precursor.backend",
                "service",
                "start",
            ]
            if was_running
            else None
        ),
        "tray_command": windows_command(TRAY) if tray_was_running else None,
        "cwd": str(supervisor.working_dir()),
        "log": str(logs / WINDOWS_UPDATE_LOG),
        "result": str(result),
        "from_version": info.current_version,
        "to_version": info.latest_version,
    }
    job_file = logs / "windows-update.json"
    job_file.write_text(json.dumps(job, indent=2), encoding="utf-8")
    winproc.spawn_detached(
        [_base_interpreter(), "-I", str(helper), str(job_file)],
        cwd=str(logs),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    invalidate()
    restarting = " and restarts it" if was_running else ""
    return (
        f"Installing {info.latest_version or 'the latest build'} in the background — "
        f"Windows can't replace Precursor while it runs, so it is stopped until the "
        f"install finishes{restarting}. Progress: {logs / WINDOWS_UPDATE_LOG}"
    )


def take_windows_update_result() -> dict[str, Any] | None:
    """The outcome the Windows updater left behind, consumed on read."""
    path = Path(get_settings().data_dir) / WINDOWS_UPDATE_RESULT
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    return raw if isinstance(raw, dict) else None


def apply(info: UpdateInfo | None = None) -> str:
    """Upgrade the installation in place. Returns a human-readable summary.

    Does **not** restart anything — the caller owns the process lifecycle (see
    ``precursor.backend.supervisor``) — except where
    :func:`applies_out_of_process` says the update finishes in a helper.
    """
    info = info or check(force=True)
    mode = info.install_mode

    if mode == "source":
        root = repo_root()
        _run(["git", "pull", "--ff-only"], cwd=root)
        # The SPA is rebuilt automatically on the next start when it is stale,
        # and a plugin's UI ships inside its own package — so there is nothing
        # else to build here.
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

    if info.channel == "nightly" and not info.wheel_url:
        raise UpdateError("The nightly release did not advertise a wheel to install.")

    extras = _extras()
    with_arguments = _with_arguments(info)
    summary = f"Installed {info.latest_version or 'the latest build'}."
    if applies_out_of_process(info):  # pragma: no cover - Windows-only path
        optional = plugin_extras(extras)
        commands = [_install_cmd(uv, info, extras, with_arguments)]
        summaries = [summary]
        if optional or with_arguments:
            dropped = [*optional, *(uv_receipt.canonical_name(a) for a in with_arguments)]
            commands.append(_install_cmd(uv, info, [e for e in extras if e not in optional], ()))
            summaries.append(
                f"{summary} Skipped {', '.join(dropped)} — not installable from your index."
            )
        return _apply_out_of_process(info, commands, summaries)
    try:
        _run(_install_cmd(uv, info, extras, with_arguments))
    except UpdateError as exc:
        # A plugin the configured index doesn't carry (a restricted mirror, a
        # release it hasn't ingested yet) used to strand the host on its old
        # build. Retry without the optional plugins and say so: an updated host
        # missing one board beats no update at all.
        optional = plugin_extras(extras)
        if not optional and not with_arguments:
            raise
        try:
            _run(_install_cmd(uv, info, [e for e in extras if e not in optional], ()))
        except UpdateError:
            # Dropping them didn't help, so they were never the problem — the
            # first failure is the honest one to report.
            raise exc from None
        dropped = [*optional, *(uv_receipt.canonical_name(a) for a in with_arguments)]
        logger.warning("Updated without the %s plugin(s): %s", ", ".join(dropped), exc)
        invalidate()
        return f"{summary} Skipped {', '.join(dropped)} — not installable from your index: {exc}"

    invalidate()
    return summary
