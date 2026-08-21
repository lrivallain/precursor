"""Self-hosted draw.io webapp — fetched on demand into the data dir.

Editing a ``.drawio`` file embeds the diagrams.net editor. We serve it from
this instance rather than from ``embed.diagrams.net`` so diagram content never
leaves the machine and the editor keeps working offline. The trade-off is size:
the release archive is ~53 MB and expands to ~150 MB, far too much to bundle in
the wheel, so it is installed on demand under ``<data_dir>/drawio/<version>``
and served by :mod:`precursor.backend.routers.drawio`.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from precursor.backend.config import get_settings
from precursor.backend.schemas.drawio import DrawioStatus

logger = logging.getLogger(__name__)

# Java servlet plumbing in the ``.war`` that means nothing to a static host.
_SKIP_TOP_LEVEL = {"WEB-INF", "META-INF"}

_CHUNK = 1 << 20


@dataclass
class _Progress:
    step: Literal["idle", "download", "extract"] = "idle"
    downloaded: int = 0
    total: int = 0
    error: str | None = None


_progress = _Progress()
_task: asyncio.Task[None] | None = None


def install_dir() -> Path:
    """Directory the pinned release is extracted to."""
    cfg = get_settings()
    return Path(cfg.drawio_dir) / cfg.drawio_version


def is_installed() -> bool:
    return (install_dir() / "index.html").is_file()


def installing() -> bool:
    return _task is not None and not _task.done()


def asset_path(rel: str) -> Path | None:
    """Resolve ``rel`` inside the install dir, or ``None`` when unavailable.

    Guards against path traversal the same way the docs/SPA static routes do.
    """
    root = install_dir().resolve()
    candidate = (root / (rel or "index.html")).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def status() -> DrawioStatus:
    cfg = get_settings()
    return DrawioStatus(
        version=cfg.drawio_version,
        installed=is_installed(),
        step=_progress.step,
        downloaded_bytes=_progress.downloaded,
        total_bytes=_progress.total,
        error=_progress.error,
        path=str(install_dir()),
    )


def start_install() -> DrawioStatus:
    """Kick off the download in the background; safe to call repeatedly."""
    global _task
    if is_installed() or installing():
        return status()
    _progress.step = "download"
    _progress.downloaded = 0
    _progress.total = 0
    _progress.error = None
    _task = asyncio.create_task(_install())
    return status()


async def _install() -> None:
    cfg = get_settings()
    version = cfg.drawio_version
    target = install_dir()
    parent = target.parent
    staging = parent / f".{version}.staging"
    archive = parent / f".{version}.war"

    parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)

    try:
        url = cfg.drawio_download_url.format(version=version)
        logger.info("Downloading draw.io %s from %s", version, url)
        timeout = httpx.Timeout(30.0, read=300.0)
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            _progress.total = int(response.headers.get("content-length") or 0)
            with archive.open("wb") as fh:
                async for chunk in response.aiter_bytes(_CHUNK):
                    fh.write(chunk)
                    _progress.downloaded += len(chunk)

        _progress.step = "extract"
        await asyncio.to_thread(_extract, archive, staging)

        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
        _prune_other_versions(parent, version)
        logger.info("draw.io %s installed at %s", version, target)
    except Exception as exc:
        logger.warning("draw.io install failed: %s", exc)
        _progress.error = str(exc) or exc.__class__.__name__
        shutil.rmtree(staging, ignore_errors=True)
    finally:
        archive.unlink(missing_ok=True)
        _progress.step = "idle"


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.filename.split("/", 1)[0] in _SKIP_TOP_LEVEL:
                continue
            out = (root / info.filename).resolve()
            if not out.is_relative_to(root):
                raise ValueError(f"Archive entry escapes the target dir: {info.filename}")
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    if not (root / "index.html").is_file():
        raise ValueError("Archive did not contain the draw.io webapp (no index.html)")


def _prune_other_versions(parent: Path, keep: str) -> None:
    """Drop superseded installs — each one costs ~150 MB of disk."""
    for entry in parent.iterdir():
        if entry.is_dir() and entry.name != keep:
            shutil.rmtree(entry, ignore_errors=True)
