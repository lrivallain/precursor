"""Resolve a usable GitHub token.

Priority:
1. ``GITHUB_TOKEN`` environment variable (already loaded into ``Settings``).
2. ``gh auth token`` output, if the GitHub CLI is installed and signed in.

The CLI result is cached for the lifetime of the process — if the user runs
``gh auth login`` while Precursor is running, restart the server.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from functools import lru_cache
from typing import Literal

from precursor.backend.config import Settings

logger = logging.getLogger("precursor.github_auth")

TokenSource = Literal["env", "gh-cli", "none"]


@lru_cache(maxsize=1)
def _gh_cli_token() -> str:
    if shutil.which("gh") is None:
        return ""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("gh auth token failed: %s", exc)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def resolve_github_token(settings: Settings) -> str:
    if settings.github_token:
        return settings.github_token
    return _gh_cli_token()


def github_token_source(settings: Settings) -> TokenSource:
    if settings.github_token:
        return "env"
    if _gh_cli_token():
        return "gh-cli"
    return "none"
