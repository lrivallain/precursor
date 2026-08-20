"""draw.io asset schemas — install state for the self-hosted editor."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DrawioStatus(BaseModel):
    """State of the on-demand draw.io webapp install."""

    # Pinned release the app serves (e.g. "v31.3.1").
    version: str
    installed: bool = False
    # Progress of an in-flight install; "idle" once it settles either way.
    step: Literal["idle", "download", "extract"] = "idle"
    downloaded_bytes: int = 0
    total_bytes: int = 0
    # Last failure, cleared when a new install starts or one succeeds.
    error: str | None = None
    # Where the assets live on disk, for troubleshooting.
    path: str
