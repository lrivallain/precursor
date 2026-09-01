"""Background ticker that keeps the WorkIQ-family OAuth sessions alive.

WorkIQ preview mode holds an OAuth access token (persisted in ``AppSetting`` by
:mod:`precursor.backend.services.mcp.workiq_preview`). Left idle, the access
token expires and the *next* WorkIQ request has to drive a silent refresh — or,
if the refresh token itself has aged out, an interactive browser sign-in. Users
saw that interactive prompt far too often.

This ticker keeps the session warm on the backend: every
``workiq_keepalive_poll_seconds`` it checks the stored token's expiry and, once
it is within :func:`~precursor.backend.services.mcp.workiq_preview.renewal_lead_seconds`
of expiring — a lead derived from the token's own lifetime, not configured —
drives a silent refresh (:func:`resolve_workiq_bearer_token`, which persists the
fresh token). It only does work while preview is enabled **and** a token exists,
so it never triggers a sign-in on its own — a machine that never signed in stays
untouched.

When a silent refresh can no longer proceed (the refresh token needs an
interactive sign-in), we emit :func:`publish_mcp_auth_required` **once** so the
app-global ``McpAuthBanner`` offers the re-authenticate action — the same UX a
live turn gets — without spamming the banner on every tick. Note a Conditional
Access "sign-in frequency" policy is an absolute window no keep-alive can defeat;
in that case this simply surfaces the re-auth prompt promptly.

The same treatment applies to every OAuth profile Precursor holds tokens for —
the hosted WorkIQ preview plus the Agent 365 servers (``workiq-teams`` /
``workiq-user``) — each *credential* tracked independently so one expired session
doesn't mask another. The Agent 365 pair shares a token, so it's kept warm once
per tick rather than once per server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from mcp.shared.auth import OAuthToken

from precursor.backend.config import Settings, get_settings
from precursor.backend.services.events import publish_mcp_auth_required
from precursor.backend.services.mcp import auth_trace
from precursor.backend.services.mcp.client import get_mcp_client_manager
from precursor.backend.services.mcp.usage import is_idle, seconds_since_use
from precursor.backend.services.mcp.workiq_preview import (
    DbTokenStorage,
    WorkIQOAuthProfile,
    _stored_token_expiry,
    renewal_lead_seconds,
    resolve_workiq_bearer_token,
)

logger = logging.getLogger(__name__)


def _auth_required_message(profile: WorkIQOAuthProfile) -> str:
    return f"{profile.label} sign-in expired. Re-authenticate to keep the session active."


class WorkIQKeepAlive:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # Edge-trigger the auth-required banner *per credential*: publish once
        # when a silent refresh starts failing, then stay quiet until it
        # succeeds. Keyed by credential rather than server so servers sharing a
        # token raise one prompt, not one each.
        self._auth_required_notified: set[str] = set()
        # Last conclusion each credential's tick reached, so an unchanged verdict
        # isn't re-reported every minute forever (see ``_verdict_changed``).
        self._last_verdict: dict[str, str] = {}

    async def start(self) -> None:
        if self._running or not self._settings.workiq_keepalive_enabled:
            if not self._settings.workiq_keepalive_enabled:
                logger.info("WorkIQ keep-alive disabled via settings.")
            return
        self._running = True
        self._auth_required_notified.clear()
        self._last_verdict.clear()
        self._task = asyncio.create_task(self._ticker(), name="workiq-keepalive")
        logger.info(
            "WorkIQ keep-alive started (poll=%ss).",
            self._settings.workiq_keepalive_poll_seconds,
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
        for profile in await self._profiles():
            try:
                await self._tick_profile(profile)
            except Exception:
                logger.exception("WorkIQ keep-alive failed for %s", profile.server)

    async def _profiles(self) -> list[WorkIQOAuthProfile]:
        """Every OAuth profile worth keeping warm on this tick.

        Deduplicated by credential so a token shared between servers is
        refreshed once per tick, not once per server. The registry decides what
        is currently signable — the preview profile only counts while preview
        mode is on (stdio mode has no token to refresh), the Agent 365 profiles
        only once a tenant resolves.
        """
        from precursor.backend.services.mcp.oauth_registry import unique_credentials

        return await unique_credentials()

    async def _tick_profile(self, profile: WorkIQOAuthProfile) -> None:
        # No stored token → the user never signed in. Do nothing (a background
        # tick must never start an interactive flow), whether idle or active.
        tokens = await DbTokenStorage(profile).get_tokens()
        if tokens is None:
            if self._verdict_changed(profile, "no-token"):
                auth_trace.record(
                    profile.server,
                    "keep-alive: nothing stored, nothing to keep warm",
                    level=logging.DEBUG,
                )
            return

        idle_after = self._settings.workiq_keepalive_idle_after_seconds
        idle_seconds = round(seconds_since_use(profile.auth_family))
        expiry = await _stored_token_expiry(tokens, profile)
        ttl = None if expiry is None else round((expiry - datetime.now(UTC)).total_seconds())
        lead = renewal_lead_seconds(tokens)
        # One line per credential per tick, at DEBUG: the whole reason a session
        # was (or wasn't) kept warm, so a lapse can be read backwards from the
        # moment it happened rather than inferred from its absence.
        facts: dict[str, Any] = {
            "expires_at": expiry.isoformat() if expiry else None,
            "ttl_seconds": ttl,
            "renewal_lead_seconds": round(lead),
            "idle_seconds": idle_seconds,
            "idle_after_seconds": idle_after,
            "has_refresh_token": bool(tokens.refresh_token),
        }

        if is_idle(profile.auth_family, idle_after):
            if self._verdict_changed(profile, "idle"):
                auth_trace.record(
                    profile.server, "keep-alive: credential is idle", level=logging.DEBUG, **facts
                )
            await self._tick_idle(profile, tokens)
            return

        if expiry is not None and not self._due_for_refresh(expiry, lead):
            if self._verdict_changed(profile, "fresh"):
                auth_trace.record(
                    profile.server,
                    "keep-alive: token still fresh, no action",
                    level=logging.DEBUG,
                    **facts,
                )
            return

        # Near expiry (or expiry unknown for a legacy token) → silently refresh.
        # ``expiry`` is passed along so the no-refresh-token short-circuit can't
        # misfire on a legacy token that may well still be valid.
        self._verdict_changed(profile, "refreshing")
        auth_trace.record(profile.server, "keep-alive: refreshing before expiry", **facts)
        await self._silent_refresh(profile, tokens, expiry)

    def _verdict_changed(self, profile: WorkIQOAuthProfile, verdict: str) -> bool:
        """Whether this tick reached a *different* conclusion than the last one.

        The ticker runs once a minute forever, and in the steady state every tick
        reaches the same harmless verdict. Recording each one would bury the
        handful of lines that explain a sign-in prompt under thousands of
        heartbeats — in the log *and* in the diagnostics buffer. Only transitions
        are worth keeping; a genuine action (an actual refresh) records
        regardless, because it isn't a verdict, it's an event.
        """
        if self._last_verdict.get(profile.auth_family) == verdict:
            return False
        self._last_verdict[profile.auth_family] = verdict
        return True

    async def _tick_idle(self, profile: WorkIQOAuthProfile, tokens: OAuthToken) -> None:
        """Handle a credential nobody has used within the idle window.

        Idle sessions are *not* kept warm — refreshing a session no one is using
        is wasted work, and prompting for one is the least welcome nag we raise.
        But we still surface a *genuine* lapse: once the stored access token has
        actually expired, probe it once so a dead refresh token raises the
        re-authenticate banner proactively, instead of ambushing the user's next
        request with a slow, silent stall. A still-refreshable idle session
        recovers quietly with no prompt. Opt out via
        ``workiq_keepalive_surface_idle_lapse``.
        """
        if not self._settings.workiq_keepalive_surface_idle_lapse:
            logger.debug("WorkIQ keep-alive: skipping idle credential for %s", profile.server)
            return
        if profile.auth_family in self._auth_required_notified:
            # Already surfaced this lapse — don't re-probe a dead token each tick.
            return
        expiry = await _stored_token_expiry(tokens, profile)
        if expiry is None or expiry > datetime.now(UTC):
            # Unknown expiry (legacy token) or still valid → leave it untouched.
            return
        # Access token has demonstrably expired → probe once to learn whether it
        # now needs an interactive sign-in.
        auth_trace.record(
            profile.server, "keep-alive: idle credential has expired, probing it once"
        )
        await self._silent_refresh(profile, tokens, expiry)

    async def _silent_refresh(
        self, profile: WorkIQOAuthProfile, tokens: OAuthToken, expiry: datetime | None
    ) -> None:
        """Drive a silent refresh, or short-circuit one that cannot succeed.

        A credential stored *without* a ``refresh_token`` — every WorkIQ sign-in
        predating the ``offline_access`` request — has nothing left to refresh
        with, so skip the round trip that can only fail and raise the
        re-authenticate prompt directly. That's also the upgrade path for those
        installs: the sign-in it asks for mints a renewable credential. It only
        applies when ``expiry`` is known, i.e. when the access token is
        established to be genuinely at or past due; a legacy token with no
        derivable expiry may well still be valid, so let the real probe decide.
        """
        if expiry is not None and not tokens.refresh_token:
            logger.info(
                "WorkIQ keep-alive: stored %s credential has no refresh token; "
                "requesting an interactive sign-in.",
                profile.server,
            )
            auth_trace.record(
                profile.server,
                "keep-alive: credential has no refresh token — renewal is impossible",
                level=logging.WARNING,
            )
            await self._on_refresh_failed(profile)
            return
        result = await resolve_workiq_bearer_token(profile, caller="keep-alive")
        if result is None:
            await self._on_refresh_failed(profile)
            return
        access_token, renewed_expiry = result
        # "Renewed" has to be observed, not assumed: a refresh the SDK declined
        # to make, or one that fell back to the stored token after a transport
        # error, returns exactly what we already had.
        renewed = access_token != tokens.access_token or (
            renewed_expiry is not None and expiry is not None and renewed_expiry > expiry
        )
        if renewed:
            await self._on_refresh_ok(profile, renewed_expiry)
        else:
            await self._on_refresh_noop(profile, renewed_expiry)

    def _due_for_refresh(self, expiry: datetime, lead: float) -> bool:
        return (expiry - datetime.now(UTC)).total_seconds() <= lead

    async def _on_refresh_failed(self, profile: WorkIQOAuthProfile) -> None:
        # Feed the client manager's short-circuit verdict either way (even if the
        # banner was already raised) so the turn path fast-fails the doomed
        # connect for this credential.
        get_mcp_client_manager().mark_auth_required(
            profile.server, message=_auth_required_message(profile)
        )
        if profile.auth_family in self._auth_required_notified:
            auth_trace.record(
                profile.server,
                "keep-alive: still unrenewable, banner already raised",
                level=logging.DEBUG,
            )
            return
        self._auth_required_notified.add(profile.auth_family)
        logger.info(
            "WorkIQ keep-alive: silent refresh for %s needs interactive sign-in.",
            profile.server,
        )
        # The moment a background tick turns into a user-visible interruption.
        auth_trace.begin_episode(profile.server, "keep-alive could not renew silently")
        auth_trace.record(profile.server, "publishing mcp.auth_required to the SPA")
        await publish_mcp_auth_required(profile.server, _auth_required_message(profile))

    async def _on_refresh_ok(self, profile: WorkIQOAuthProfile, expiry: datetime | None) -> None:
        recovered = profile.auth_family in self._auth_required_notified
        self._auth_required_notified.discard(profile.auth_family)
        # The credential is signable again — let connects proceed for real.
        get_mcp_client_manager().clear_auth_required(profile.server)
        logger.debug(
            "WorkIQ keep-alive refreshed %s token (expires=%s).",
            profile.server,
            expiry.isoformat() if expiry else "unknown",
        )
        auth_trace.record(
            profile.server,
            "keep-alive: token renewed",
            level=logging.INFO if recovered else logging.DEBUG,
            expires_at=expiry.isoformat() if expiry else None,
            recovered_from_lapse=recovered,
        )
        if recovered:
            auth_trace.end_episode(profile.server, "recovered without prompting")

    async def _on_refresh_noop(self, profile: WorkIQOAuthProfile, expiry: datetime | None) -> None:
        """A refresh completed without renewing anything.

        The session was opened, nothing refused it, and the same token came back
        — a transport error that fell back to the stored credential, or a
        renewal the SDK declined to make. Either way the token is still marching
        towards its original expiry, so this is deliberately *not* treated as a
        recovery: no banner is cleared and no episode is closed on the strength
        of work that did not happen. Reporting these as renewals is exactly how a
        credential drifts past expiry with the trace insisting all is well.
        """
        logger.debug(
            "WorkIQ keep-alive left %s token unchanged (expires=%s).",
            profile.server,
            expiry.isoformat() if expiry else "unknown",
        )
        auth_trace.record(
            profile.server,
            "keep-alive: refresh returned the same token — nothing was renewed",
            level=logging.WARNING,
            expires_at=expiry.isoformat() if expiry else None,
        )


_keepalive: WorkIQKeepAlive | None = None


def get_workiq_keepalive() -> WorkIQKeepAlive:
    global _keepalive
    if _keepalive is None:
        _keepalive = WorkIQKeepAlive()
    return _keepalive
