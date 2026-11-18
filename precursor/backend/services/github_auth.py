"""Resolve a usable GitHub token.

Priority:
1. ``github_token`` saved in the app settings (``api_keys`` in the DB).
2. ``gh auth token`` output, if the GitHub CLI is installed and signed in.
   With several logins, the account picked in Settings (``github_cli_account``)
   wins, then ``PRECURSOR_GITHUB_CLI_USER``, then the CLI's active account.

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
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from precursor.backend import winproc
from precursor.backend.config import get_settings
from precursor.backend.models import AppSetting

logger = logging.getLogger(__name__)

TokenSource = Literal["settings", "gh-cli", "none"]

# Long enough to keep `gh` off the hot path, short enough that `gh auth login`
# (or a rotated token) is picked up without restarting Precursor.
_GH_TOKEN_TTL_SECONDS = 300.0

# The AppSetting key holding the `gh` login picked in Settings ("" = default).
GITHUB_CLI_ACCOUNT_KEY = "github_cli_account"

_gh_token_lock = threading.Lock()
# Keyed by the pinned login ("" for the CLI's active account), so switching
# accounts in Settings never serves the previous account's token.
_gh_token_cache: dict[str, tuple[float, str]] = {}


@dataclass(frozen=True, slots=True)
class GhAccount:
    login: str
    host: str
    active: bool


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["gh", *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
            # The tray resolves a token too, and it has no console to lend.
            **winproc.no_window(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("gh %s failed: %s", " ".join(args[:2]), exc)
        return None


def _run_gh_auth_token(user: str | None = None) -> str:
    if shutil.which("gh") is None:
        return ""
    # `gh auth token` follows the CLI's *active* account, so with several logins
    # the answer depends on whoever last ran `gh auth switch`. Pinning the login
    # makes the resolution deterministic and removes that step from any
    # launcher/startup sequence.
    cmd = ["auth", "token"]
    if user is None:
        user = get_settings().github_cli_user
    user = user.strip()
    if user:
        cmd += ["--user", user]
    result = _run_gh(cmd)
    if result is None:
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


def _gh_cli_token(user: str = "") -> str:
    with _gh_token_lock:
        cached = _gh_token_cache.get(user)
        if cached is not None and (time.monotonic() - cached[0]) < _GH_TOKEN_TTL_SECONDS:
            return cached[1]
        token = _run_gh_auth_token(user or None)
        _gh_token_cache[user] = (time.monotonic(), token)
        return token


def invalidate_gh_cli_token() -> None:
    """Drop the cached CLI tokens so the next read re-runs ``gh auth token``."""
    with _gh_token_lock:
        _gh_token_cache.clear()


def _parse_gh_accounts(stdout: str) -> list[GhAccount]:
    try:
        hosts = json.loads(stdout).get("hosts")
    except (json.JSONDecodeError, AttributeError):
        return []
    accounts: list[GhAccount] = []
    # Only github.com: that's the host every token consumer here talks to.
    for entry in (hosts or {}).get("github.com") or []:
        login = entry.get("login") if isinstance(entry, dict) else None
        if isinstance(login, str) and login and entry.get("state", "success") == "success":
            accounts.append(
                GhAccount(login=login, host="github.com", active=bool(entry.get("active")))
            )
    return accounts


def list_gh_accounts() -> list[GhAccount]:
    """Every github.com login signed in to the GitHub CLI (none if gh is absent).

    Needs a ``gh`` recent enough for ``auth status --json``; older ones list
    nothing, which leaves the env pin and the active account as the only options.
    """
    if shutil.which("gh") is None:
        return []
    result = _run_gh(["auth", "status", "--json", "hosts", "--hostname", "github.com"])
    if result is None or result.returncode != 0:
        return []
    return _parse_gh_accounts(result.stdout)


async def resolve_github_cli_account(session: AsyncSession) -> str:
    """The `gh` login picked in Settings, or "" to fall back to the env pin."""
    row = await session.get(AppSetting, GITHUB_CLI_ACCOUNT_KEY)
    if row is None:
        return ""
    try:
        value = json.loads(row.value)
    except json.JSONDecodeError:
        return ""
    return value.strip() if isinstance(value, str) else ""


async def _effective_cli_user(session: AsyncSession) -> str:
    return await resolve_github_cli_account(session) or get_settings().github_cli_user.strip()


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
    return _gh_cli_token(await _effective_cli_user(session))


async def github_token_source(session: AsyncSession) -> TokenSource:
    """Where the effective token comes from (drives the Settings UI hint)."""
    if await _settings_github_token(session):
        return "settings"
    if _gh_cli_token(await _effective_cli_user(session)):
        return "gh-cli"
    return "none"
