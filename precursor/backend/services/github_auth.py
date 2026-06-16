"""Resolve a usable GitHub token.

Priority:
1. ``github_token`` saved in the app settings (``api_keys`` in the DB).
2. ``gh auth token`` output, if the GitHub CLI is installed and signed in.

The CLI result is cached for the lifetime of the process — if the user runs
``gh auth login`` while Precursor is running, restart the server.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from functools import lru_cache
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.models import AppSetting

logger = logging.getLogger(__name__)

TokenSource = Literal["settings", "gh-cli", "none"]


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


async def _settings_github_token(session: AsyncSession) -> str:
    row = await session.get(AppSetting, "api_keys")
    if row is None:
        return ""
    try:
        api_keys = json.loads(row.value)
    except json.JSONDecodeError:
        return ""
    if isinstance(api_keys, dict):
        token = api_keys.get("github_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return ""


async def resolve_github_token(session: AsyncSession) -> str:
    """Return the effective GitHub token: saved settings, else the gh CLI."""
    token = await _settings_github_token(session)
    if token:
        return token
    return _gh_cli_token()


async def github_token_source(session: AsyncSession) -> TokenSource:
    """Where the effective token comes from (drives the Settings UI hint)."""
    if await _settings_github_token(session):
        return "settings"
    if _gh_cli_token():
        return "gh-cli"
    return "none"
