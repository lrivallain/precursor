"""Version endpoint — surfaces the running app version for the UI 'About' line."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from precursor import __version__
from precursor.backend.services import updates

router = APIRouter(prefix="/api/version", tags=["version"])


class VersionInfo(BaseModel):
    version: str
    # Parsed from the local part of a dev version (e.g. "...+g<sha>.d<date>"),
    # null for clean tagged releases.
    commit: str | None = None
    build_date: str | None = None


def _parse_local(v: str) -> tuple[str | None, str | None]:
    """Pull commit sha + build date out of a hatch-vcs dev version string."""
    if "+" not in v:
        return None, None
    commit: str | None = None
    build_date: str | None = None
    for part in v.split("+", 1)[1].split("."):
        if part.startswith("g") and len(part) > 1:
            commit = part[1:]
        elif part.startswith("d") and part[1:].isdigit():
            build_date = part[1:]
    return commit, build_date


def version_info() -> VersionInfo:
    commit, build_date = _parse_local(__version__)
    return VersionInfo(version=__version__, commit=commit, build_date=build_date)


@router.get("", response_model=VersionInfo)
async def get_version() -> VersionInfo:
    return version_info()


class UpdateCheck(BaseModel):
    current_version: str
    current_commit: str | None = None
    latest_version: str | None = None
    latest_commit: str | None = None
    update_available: bool = False
    # "stable" (tagged releases) or "nightly" (rolling build from main).
    channel: str
    # "source", "uv-tool" or "wheel" — only the first two can self-update.
    install_mode: str
    release_url: str | None = None
    # Set when the lookup itself failed, so the UI can distinguish "no update"
    # from "couldn't ask".
    error: str | None = None


@router.get("/check", response_model=UpdateCheck)
async def check_update(force: bool = False) -> UpdateCheck:
    """Report whether a newer build is published on the active channel.

    Read-only on purpose: applying an update replaces the very process serving
    this request, so it belongs to the supervisor (``precursor service update``
    or the tray), not to an HTTP handler.
    """
    info = await run_in_threadpool(updates.check, force=force)
    return UpdateCheck(
        current_version=info.current_version,
        current_commit=info.current_commit,
        latest_version=info.latest_version,
        latest_commit=info.latest_commit,
        update_available=info.update_available,
        channel=info.channel,
        install_mode=info.install_mode,
        release_url=info.release_url,
        error=info.error,
    )
