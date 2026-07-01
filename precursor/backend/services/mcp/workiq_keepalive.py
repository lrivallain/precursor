"""Background ticker that keeps the WorkIQ preview OAuth session alive.

WorkIQ preview mode holds an OAuth access token (persisted in ``AppSetting`` by
:mod:`precursor.backend.services.mcp.workiq_preview`). Left idle, the access
token expires and the *next* WorkIQ request has to drive a silent refresh — or,
if the refresh token itself has aged out, an interactive browser sign-in. Users
saw that interactive prompt far too often.

This ticker keeps the session warm on the backend: every
``workiq_keepalive_poll_seconds`` it checks the stored token's expiry and, once
it's within ``workiq_keepalive_refresh_margin_seconds`` of expiring, drives a
silent refresh (:func:`resolve_workiq_bearer_token`, which persists the fresh
token). It only does work while preview is enabled **and** a token exists, so it
never triggers a sign-in on its own — a machine that never signed in stays
untouched.

When a silent refresh can no longer proceed (the refresh token needs an
interactive sign-in), we emit :func:`publish_mcp_auth_required` **once** so the
app-global ``McpAuthBanner`` offers the re-authenticate action — the same UX a
live turn gets — without spamming the banner on every tick. Note a Conditional
Access "sign-in frequency" policy is an absolute window no keep-alive can defeat;
in that case this simply surfaces the re-auth prompt promptly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from precursor.backend.config import Settings, get_settings
from precursor.backend.services.events import publish_mcp_auth_required
from precursor.backend.services.mcp.workiq_preview import (
    DbTokenStorage,
    _stored_token_expiry,
    resolve_workiq_bearer_token,
    resolve_workiq_preview,
)

logger = logging.getLogger(__name__)

_AUTH_REQUIRED_MESSAGE = (
    "WorkIQ sign-in expired. Re-authenticate to keep the preview session active."
)


class WorkIQKeepAlive:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # Edge-trigger the auth-required banner: publish once when a silent
        # refresh starts failing, then stay quiet until it succeeds again.
        self._auth_required_notified = False

    async def start(self) -> None:
        if self._running or not self._settings.workiq_keepalive_enabled:
            if not self._settings.workiq_keepalive_enabled:
                logger.info("WorkIQ keep-alive disabled via settings.")
            return
        self._running = True
        self._auth_required_notified = False
        self._task = asyncio.create_task(self._ticker(), name="workiq-keepalive")
        logger.info(
            "WorkIQ keep-alive started (poll=%ss, margin=%ss).",
            self._settings.workiq_keepalive_poll_seconds,
            self._settings.workiq_keepalive_refresh_margin_seconds,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _ticker(self) -> None:
        poll = max(15, self._settings.workiq_keepalive_poll_seconds)
        while self._running:
            try:
                await self._tick_once()
            except Exception:
                logger.exception("WorkIQ keep-alive iteration failed")
            try:
                await asyncio.sleep(poll)
            except asyncio.CancelledError:
                break

    async def _tick_once(self) -> None:
        # Only keep a preview session alive: stdio (local) mode has no OAuth
        # token to refresh.
        if not await resolve_workiq_preview():
            return
        # No stored token → the user hasn't signed in. Do nothing (never start
        # an interactive flow from a background tick).
        tokens = await DbTokenStorage().get_tokens()
        if tokens is None:
            return

        expiry = await _stored_token_expiry(tokens)
        if expiry is not None and not self._due_for_refresh(expiry):
            return

        # Near expiry (or expiry unknown for a legacy token) → silently refresh.
        result = await resolve_workiq_bearer_token()
        if result is None:
            await self._on_refresh_failed()
        else:
            await self._on_refresh_ok(result[1])

    def _due_for_refresh(self, expiry: datetime) -> bool:
        margin = self._settings.workiq_keepalive_refresh_margin_seconds
        return (expiry - datetime.now(UTC)).total_seconds() <= margin

    async def _on_refresh_failed(self) -> None:
        if self._auth_required_notified:
            return
        self._auth_required_notified = True
        logger.info("WorkIQ keep-alive: silent refresh needs interactive sign-in.")
        await publish_mcp_auth_required("workiq", _AUTH_REQUIRED_MESSAGE)

    async def _on_refresh_ok(self, expiry: datetime | None) -> None:
        self._auth_required_notified = False
        logger.debug(
            "WorkIQ keep-alive refreshed token (expires=%s).",
            expiry.isoformat() if expiry else "unknown",
        )


_keepalive: WorkIQKeepAlive | None = None


def get_workiq_keepalive() -> WorkIQKeepAlive:
    global _keepalive
    if _keepalive is None:
        _keepalive = WorkIQKeepAlive()
    return _keepalive
