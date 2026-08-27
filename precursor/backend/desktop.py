"""Hand a path to the desktop's file manager.

Small and platform-shaped on purpose: "show me where that lives" is a question
the background app answers twice — once from the tray menu, once from
``precursor service data-dir --reveal`` — and neither should carry its own copy
of the platform quirks.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class RevealError(RuntimeError):
    """The path could not be shown in a file manager."""


def _opener() -> list[str] | None:
    if sys.platform == "darwin":
        return ["open"]
    if os.name == "nt":  # pragma: no cover - Windows-only path
        return ["explorer"]
    # Freedesktop systems: xdg-open dispatches to whatever file manager is
    # configured. A headless box has neither, which is a clear "not supported"
    # rather than an error worth raising.
    return ["xdg-open"] if shutil.which("xdg-open") else None


def reveal(path: Path) -> None:
    """Open ``path`` in the platform file manager, creating it if needed.

    The data directory is created lazily by whichever feature writes to it
    first, so on a fresh install it may not exist yet — and "nothing happened"
    is a worse answer than an empty folder.
    """
    opener = _opener()
    if opener is None:
        raise RevealError(
            f"No file manager integration is available on {sys.platform}. The path is: {path}"
        )

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RevealError(f"Could not create {path}: {exc}") from exc

    try:
        result = subprocess.run(
            [*opener, str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RevealError(f"Could not open {path}: {exc}") from exc

    # Explorer reports failure (exit 1) even when it opened the window, so its
    # exit code says nothing and checking it would break the common case.
    if result.returncode != 0 and os.name != "nt":
        raise RevealError(
            f"`{opener[0]}` failed for {path}: "
            f"{result.stderr.strip() or f'exit {result.returncode}'}"
        )
