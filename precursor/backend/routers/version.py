"""Version endpoint — surfaces the running app version for the UI 'About' line."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from precursor import __version__

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
