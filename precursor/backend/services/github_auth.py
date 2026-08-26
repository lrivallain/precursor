"""Resolve a usable GitHub token.

Priority:
1. ``github_token`` saved in the app settings (``api_keys`` in the DB).
2. ``gh auth token`` output, if the GitHub CLI is installed and signed in.
   ``PRECURSOR_GITHUB_CLI_USER`` pins which login is used when several are
   signed in, so the result doesn't depend on the CLI's active account.

The CLI result is cached for a short TTL rather than the process lifetime:
GitHub scopes entitlements (notably the Copilot model catalogue) to the token
itself, so pinning one forever makes a long-running instance serve a snapshot
that drifts further from reality the longer it stays up.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend.config import get_settings
from precursor.backend.models import AppSetting

logger = logging.getLogger(__name__)

TokenSource = Literal["settings", "gh-cli", "none"]

# Long enough to keep `gh` off the hot path, short enough that `gh auth login`
# (or a rotated token) is picked up without restarting Precursor.
_GH_TOKEN_TTL_SECONDS = 300.0

_gh_token_lock = threading.Lock()
_gh_token_cache: tuple[float, str] | None = None


def _run_gh_auth_token() -> str:
    if shutil.which("gh") is None:
        return ""
    # `gh auth token` follows the CLI's *active* account, so with several logins
    # the answer depends on whoever last ran `gh auth switch`. Pinning the login
    # in settings makes the resolution deterministic and removes that step from
    # any launcher/startup sequence.
    cmd = ["gh", "auth", "token"]
    user = get_settings().github_cli_user.strip()
    if user:
        cmd += ["--user", user]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("gh auth token failed: %s", exc)
        return ""
    if result.returncode != 0:
        if user:
            logger.warning(
                "`gh auth token --user %s` failed (%s) — is that account signed in?",
                user,
                result.stderr.strip() or f"exit {result.returncode}",
            )
        return ""
    return result.stdout.strip()


def _gh_cli_token() -> str:
    global _gh_token_cache
    with _gh_token_lock:
        cached = _gh_token_cache
        if cached is not None and (time.monotonic() - cached[0]) < _GH_TOKEN_TTL_SECONDS:
            return cached[1]
        token = _run_gh_auth_token()
        _gh_token_cache = (time.monotonic(), token)
        return token


def invalidate_gh_cli_token() -> None:
    """Drop the cached CLI token so the next read re-runs ``gh auth token``."""
    global _gh_token_cache
    with _gh_token_lock:
        _gh_token_cache = None


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
