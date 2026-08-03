"""WorkIQ *preview* mode — OAuth-backed streamable-HTTP transport for writes.

The built-in ``workiq`` MCP server normally runs the read-only local stdio
launcher (``npx @microsoft/workiq@latest mcp``). Preview mode points it at the
hosted endpoint ``https://workiq.svc.cloud.microsoft/mcp`` instead, which serves
the full read **and write** surface (``create_entity``/``update_entity``/
``delete_entity``/``do_action``/…).

That endpoint is OAuth-protected. We drive the MCP SDK's
:class:`~mcp.client.auth.OAuthClientProvider` with the WorkIQ-published public
client id and a loopback redirect on port 12798 (matching the Copilot CLI
plugin's ``redirectPort``). Tokens are persisted in ``AppSetting`` so the
interactive browser login only happens once per machine.

The same machinery serves every OAuth-protected WorkIQ endpoint — the hosted
preview above and the Agent 365 servers in
:mod:`precursor.backend.services.mcp.agent365` — so it is parameterized by a
:class:`WorkIQOAuthProfile` (endpoint, client id, loopback port, storage keys).
Everything defaults to :data:`PREVIEW_PROFILE`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import json
import logging
import socket
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from precursor.backend.config import get_settings
from precursor.backend.db import SessionLocal
from precursor.backend.models import AppSetting
from precursor.backend.services.events import publish_mcp_auth_url

logger = logging.getLogger(__name__)


class WorkIQAuthRequiredError(RuntimeError):
    """A WorkIQ request needs an interactive sign-in we deliberately won't run.

    Background connects (catalog probes, warm-pool workers, chat turns) build a
    *non-interactive* OAuth provider: when the stored tokens are missing or can
    no longer be silently refreshed, the SDK would otherwise pop a browser and
    block the request for minutes. We raise this instead so the caller can fail
    fast and surface a ``needs_auth`` state; the user restarts the browser flow
    explicitly via :func:`reauthenticate_workiq`.
    """


class WorkIQAuthInProgressError(RuntimeError):
    """An interactive WorkIQ sign-in is already running (single-flight guard)."""


class WorkIQInteractionRequiredError(RuntimeError):
    """A silent (``prompt=none``) WorkIQ authorization needs user interaction.

    Entra answers ``prompt=none`` with ``interaction_required`` /
    ``login_required`` / ``consent_required`` / ``account_selection_required``
    when it can't complete the sign-in without showing UI (no live SSO session,
    MFA or consent due, ambiguous account, …). The loopback callback raises this
    so :func:`reauthenticate_workiq` can fall back to the visible interactive
    prompt instead of treating it as a hard failure.
    """


class WorkIQAuthPortBusyError(RuntimeError):
    """The fixed OAuth loopback port is already owned by another process.

    The redirect port (:data:`WORKIQ_OAUTH_REDIRECT_PORT`) is fixed — it has to
    match the ``redirect_uri`` registered for the WorkIQ OAuth client — so only
    one process per machine can run the loopback callback at a time. When several
    Precursor instances run side by side (e.g. multiple worktrees), a second
    interactive sign-in can't bind the port; without this guard it would clear
    the stored tokens and then strand the UI on "Signing in…" until the callback
    times out, its browser redirect having been delivered to whichever instance
    owns the port. We raise this up front so the caller can surface a clear,
    actionable error instead.
    """


class WorkIQAuthCancelledError(RuntimeError):
    """An in-flight interactive WorkIQ sign-in was cancelled by the user.

    The SPA cancels proactively when its sign-in popup is closed without
    completing (:func:`cancel_reauthenticate_workiq`), so the loopback callback
    stops waiting and frees the fixed redirect port immediately instead of
    squatting it for the full timeout — which would otherwise block a sign-in
    from any other Precursor window on the machine.
    """


class WorkIQAuthTimeoutError(RuntimeError):
    """An interactive WorkIQ sign-in the user never completed in time.

    The visible loopback waits up to :data:`_CALLBACK_TIMEOUT_SECONDS` for the
    browser redirect. When it never arrives — the user walked away, or closed the
    tab without the SPA's proactive cancel firing — that's a *benign, expected*
    outcome, not a server failure: we simply couldn't finish the grant. Raising a
    dedicated type (instead of a bare ``RuntimeError``) lets
    :class:`_SuppressExpectedAuthError` drop the SDK's misleading ERROR traceback
    and lets :func:`reauthenticate_workiq`'s caller re-surface the manual sign-in
    banner rather than reporting an opaque gateway failure. It is the interactive
    twin of :class:`WorkIQInteractionRequiredError` (the silent-pass timeout).
    """


class _SuppressExpectedAuthError(logging.Filter):
    """Drop the SDK's ERROR traceback for an *expected* WorkIQ sign-in prompt.

    The MCP SDK logs ``logger.exception("OAuth flow error")`` for any exception
    raised inside its auth flow, then re-raises. Several of those "errors" are, to
    us, normal handled signals: :class:`WorkIQAuthRequiredError` (a background
    connect refusing to pop a browser), :class:`WorkIQInteractionRequiredError`
    (a silent ``prompt=none`` pass that needs UI) and :class:`WorkIQAuthTimeoutError`
    (an interactive sign-in the user never completed). We already log those
    concisely where we handle them, so this filter strips the misleading full
    stack trace the SDK would otherwise dump for each.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc_info = record.exc_info
        if not exc_info:
            return True
        exc = exc_info[1]
        seen: set[int] = set()
        stack: list[BaseException | None] = [exc]
        while stack:
            node = stack.pop()
            if node is None or id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(
                node,
                WorkIQAuthRequiredError | WorkIQInteractionRequiredError | WorkIQAuthTimeoutError,
            ):
                return False
            if isinstance(node, BaseExceptionGroup):
                stack.extend(node.exceptions)
            stack.append(node.__cause__)
            stack.append(node.__context__)
        return True


logging.getLogger("mcp.client.auth.oauth2").addFilter(_SuppressExpectedAuthError())


# Hosted WorkIQ MCP endpoint (full read+write surface).
WORKIQ_PREVIEW_URL = "https://workiq.svc.cloud.microsoft/mcp"
# WorkIQ-published public OAuth client (same id the Copilot CLI plugin uses).
WORKIQ_OAUTH_CLIENT_ID = "ba081686-5d24-4bc6-a0d6-d034ecffed87"
# Loopback redirect; the port matches the plugin's ``auth.redirectPort``.
WORKIQ_OAUTH_REDIRECT_PORT = 12798
WORKIQ_OAUTH_REDIRECT_PATH = "/callback"
WORKIQ_OAUTH_REDIRECT_URI = (
    f"http://localhost:{WORKIQ_OAUTH_REDIRECT_PORT}{WORKIQ_OAUTH_REDIRECT_PATH}"
)

# AppSetting keys.
PREVIEW_FLAG_KEY = "mcp_workiq_preview"
OAUTH_TOKENS_KEY = "workiq_oauth_tokens"
# When the current tokens were last issued/refreshed. The SDK persists tokens
# without an absolute expiry, so we stamp the write time here and combine it
# with the token's relative ``expires_in`` to recover a real expiry instant.
OAUTH_ISSUED_AT_KEY = "workiq_oauth_issued_at"
# The last signed-in account name (UPN/email), captured from the access-token
# JWT. Used purely as an Entra ``login_hint`` to pre-select the account on
# re-auth — never a security decision — so it deliberately survives
# ``clear_workiq_oauth_tokens`` (which only forgets the tokens themselves).
OAUTH_LOGIN_HINT_KEY = "workiq_oauth_login_hint"


@dataclass(frozen=True, slots=True)
class WorkIQOAuthProfile:
    """Everything that distinguishes one OAuth-protected WorkIQ endpoint.

    The sign-in machinery below (token storage, loopback callback, silent and
    interactive re-auth) is identical for every WorkIQ server; only the endpoint,
    the Entra client it authenticates as, the loopback port it redirects to and
    the ``AppSetting`` keys it persists under differ. Each server gets its own
    port so two sign-ins can run side by side, and its own storage keys so their
    tokens never collide.
    """

    # MCP server name — the identity used on the event bus and in log lines.
    server: str
    # Human-facing name shown in the sign-in page and error messages.
    label: str
    # The MCP endpoint whose 401 drives the OAuth discovery.
    url: str
    client_id: str
    redirect_port: int
    client_name: str
    tokens_key: str
    issued_at_key: str
    login_hint_key: str
    # Loopback host and path of the redirect URI. Entra ignores the *port* of a
    # public client's loopback redirect, but the host and path must match the
    # registration character for character — and the two WorkIQ client apps were
    # registered differently (``localhost``/``/callback`` vs ``127.0.0.1``/``/``).
    # The callback listener binds 127.0.0.1 and ignores the path either way.
    redirect_host: str = "localhost"
    redirect_path: str = WORKIQ_OAUTH_REDIRECT_PATH

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.redirect_host}:{self.redirect_port}{self.redirect_path}"


PREVIEW_PROFILE = WorkIQOAuthProfile(
    server="workiq",
    label="WorkIQ",
    url=WORKIQ_PREVIEW_URL,
    client_id=WORKIQ_OAUTH_CLIENT_ID,
    redirect_port=WORKIQ_OAUTH_REDIRECT_PORT,
    client_name="Precursor (WorkIQ preview)",
    tokens_key=OAUTH_TOKENS_KEY,
    issued_at_key=OAUTH_ISSUED_AT_KEY,
    login_hint_key=OAUTH_LOGIN_HINT_KEY,
)

# Entra ``prompt=none`` error codes that mean "can't sign in silently, ask the
# user". Anything else from the callback is a genuine failure.
_INTERACTION_REQUIRED_ERRORS = frozenset(
    {
        "interaction_required",
        "login_required",
        "consent_required",
        "account_selection_required",
    }
)

# How long the interactive loopback waits for the browser redirect. It has to
# cover a *human* completing the Microsoft sign-in — password, account picker,
# MFA push, Conditional Access — so it can't be aggressively short, but the fixed
# loopback port is exclusive per machine, so an abandoned flow squatting it blocks
# every other Precursor window. 3 minutes comfortably covers a real sign-in while
# capping how long a walked-away flow holds the port (the SPA also cancels
# proactively when its popup closes — see ``cancel_reauthenticate_workiq``).
_CALLBACK_TIMEOUT_SECONDS = 180.0

# The hands-free silent (``prompt=none``) auto re-auth runs in an invisible SPA
# iframe with nobody watching, so it must not hold the loopback open for the full
# interactive window. A live Entra SSO session redirects the frame back near
# instantly; if framing or third-party cookies block it we want to give up fast
# and let the visible banner take over. Keep this comfortably short.
_SILENT_REAUTH_CALLBACK_TIMEOUT_SECONDS = 20.0

# Serializes interactive sign-ins so two triggers can't open competing browser
# flows fighting over a profile's single loopback redirect port. Per profile:
# different servers use different ports, so their sign-ins don't contend.
_reauth_locks: dict[str, asyncio.Lock] = {}

# Set while an interactive sign-in is waiting on the loopback redirect; the SPA
# signals it (via :func:`cancel_reauthenticate_workiq`) when its popup closes
# without completing, so the callback stops waiting and frees the fixed port at
# once instead of holding it for the full timeout. Absent when no interactive
# sign-in is in flight for that profile. Only ever touched from the single event
# loop.
_active_signin_cancels: dict[str, asyncio.Event] = {}


def _lock_for(profile: WorkIQOAuthProfile) -> asyncio.Lock:
    """The single-flight sign-in lock for ``profile``, created on first use."""
    lock = _reauth_locks.get(profile.server)
    if lock is None:
        lock = asyncio.Lock()
        _reauth_locks[profile.server] = lock
    return lock


def cancel_reauthenticate_workiq(profile: WorkIQOAuthProfile = PREVIEW_PROFILE) -> bool:
    """Ask an in-flight interactive WorkIQ sign-in to abort, freeing the port.

    Returns ``True`` when a waiting sign-in was signalled, ``False`` when none is
    in flight (or one was already signalled). A no-op once the redirect has
    arrived — a nearly-complete sign-in is allowed to finish rather than be torn
    down. Safe to call at any time; the loopback releases the fixed redirect port
    as soon as it unwinds.
    """
    event = _active_signin_cancels.get(profile.server)
    if event is None or event.is_set():
        return False
    event.set()
    return True


def _port_busy_message(profile: WorkIQOAuthProfile) -> str:
    """Message for a loopback port already owned by another process.

    Typically a second Precursor window mid sign-in on the same machine.
    """
    return (
        f"The {profile.label} sign-in port {profile.redirect_port} is already in use — "
        "another Precursor window or app is signing in. Finish or close that sign-in, "
        "then try again."
    )


def _assert_loopback_port_available(profile: WorkIQOAuthProfile = PREVIEW_PROFILE) -> None:
    """Fail fast when another process already owns the OAuth loopback port.

    The redirect port is fixed (it must match the registered ``redirect_uri``),
    so only one process on the machine can run the loopback callback at a time.
    Probing it before we clear tokens or drive the browser flow lets an
    interactive sign-in surface a clear :class:`WorkIQAuthPortBusyError` instead
    of stranding the UI on "Signing in…" until the callback times out — the
    common failure when several Precursor instances run side by side.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Mirror ``asyncio.start_server``'s default SO_REUSEADDR so the probe
        # matches the real bind: it still raises EADDRINUSE against a live
        # listener, but not against a socket merely lingering in TIME_WAIT.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", profile.redirect_port))
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, errno.EADDRNOTAVAIL, errno.EACCES):
            raise WorkIQAuthPortBusyError(_port_busy_message(profile)) from exc
        raise
    finally:
        probe.close()


async def resolve_workiq_preview() -> bool:
    """Whether WorkIQ preview (hosted HTTP + writes) is enabled."""
    async with SessionLocal() as session:
        row = await session.get(AppSetting, PREVIEW_FLAG_KEY)
        return bool(row and row.value == "true")


async def set_workiq_preview(enabled: bool) -> None:
    async with SessionLocal() as session:
        row = await session.get(AppSetting, PREVIEW_FLAG_KEY)
        encoded = "true" if enabled else "false"
        if row is None:
            session.add(AppSetting(key=PREVIEW_FLAG_KEY, value=encoded))
        else:
            row.value = encoded
        await session.commit()


class DbTokenStorage(TokenStorage):
    """OAuth token + client-info storage backed by the ``AppSetting`` table.

    ``client_info`` is fixed: we always hand the SDK the profile's pre-registered
    public client id so it skips dynamic registration (see ``OAuthClientProvider``
    step 4) — which Entra doesn't offer anyway. Only the issued tokens are
    persisted, under the profile's own keys, so a successful login survives app
    restarts without one server's tokens overwriting another's.
    """

    def __init__(self, profile: WorkIQOAuthProfile = PREVIEW_PROFILE) -> None:
        self._profile = profile
        self._client_info = OAuthClientInformationFull(
            client_id=profile.client_id,
            redirect_uris=[profile.redirect_uri],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            client_name=profile.client_name,
        )

    async def get_tokens(self) -> OAuthToken | None:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, self._profile.tokens_key)
        if row is None or not row.value or row.value == "null":
            return None
        try:
            return OAuthToken.model_validate_json(row.value)
        except ValueError:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        encoded = tokens.model_dump_json()
        # The SDK calls this whenever it issues or refreshes tokens, so "now" is
        # the moment they became valid — stamp it so we can compute their real
        # expiry later (``expires_in`` is relative to this instant). Store it
        # JSON-encoded so it satisfies the all-JSON ``AppSetting.value`` contract
        # the settings router relies on (a raw ISO string isn't valid JSON).
        issued_at = json.dumps(datetime.now(UTC).isoformat())
        # Best-effort: remember the account so we can pre-select it on re-auth.
        login_hint = _login_hint_from_access_token(tokens.access_token)
        async with SessionLocal() as session:
            row = await session.get(AppSetting, self._profile.tokens_key)
            if row is None:
                session.add(AppSetting(key=self._profile.tokens_key, value=encoded))
            else:
                row.value = encoded
            issued_row = await session.get(AppSetting, self._profile.issued_at_key)
            if issued_row is None:
                session.add(AppSetting(key=self._profile.issued_at_key, value=issued_at))
            else:
                issued_row.value = issued_at
            if login_hint:
                encoded_hint = json.dumps(login_hint)
                hint_row = await session.get(AppSetting, self._profile.login_hint_key)
                if hint_row is None:
                    session.add(AppSetting(key=self._profile.login_hint_key, value=encoded_hint))
                else:
                    hint_row.value = encoded_hint
            await session.commit()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        # The client is pre-registered; nothing to persist.
        return None


async def clear_workiq_oauth_tokens(profile: WorkIQOAuthProfile = PREVIEW_PROFILE) -> None:
    """Forget any stored tokens so the next connect re-runs the browser login.

    The captured ``login_hint`` (last account) deliberately survives: it isn't a
    credential, and keeping it lets the next re-auth pre-select the same account.
    """
    async with SessionLocal() as session:
        for key in (profile.tokens_key, profile.issued_at_key):
            row = await session.get(AppSetting, key)
            if row is not None:
                await session.delete(row)
        await session.commit()


def _claims_from_access_token(access_token: str) -> dict[str, Any] | None:
    """Best-effort decode of an Entra JWT's payload, **unverified**.

    The values we read from it (account name, tenant id) only ever drive UX —
    pre-filling the account picker, building the tenant-scoped endpoint URL — and
    are never an authorization decision, so skipping signature validation is
    safe. Returns ``None`` for an opaque or malformed token.
    """
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore stripped base64 padding
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, TypeError):
        return None
    return claims if isinstance(claims, dict) else None


def _login_hint_from_access_token(access_token: str) -> str | None:
    """Best-effort extract the signed-in user's account name from an Entra JWT.

    The WorkIQ access token is an Entra-issued JWT whose payload carries the
    user's principal name (``preferred_username`` / ``upn`` / …). We decode it
    **unverified** — the value is only ever used as a UX ``login_hint`` to
    pre-fill the account picker, never for authorization — and return ``None``
    for an opaque or malformed token.
    """
    claims = _claims_from_access_token(access_token)
    if claims is None:
        return None
    for claim in ("preferred_username", "upn", "unique_name", "email"):
        value = claims.get(claim)
        if isinstance(value, str) and value:
            return value
    return None


def _tenant_from_access_token(access_token: str) -> str | None:
    """Best-effort extract the Entra tenant id (``tid`` claim) from a JWT."""
    claims = _claims_from_access_token(access_token)
    if claims is None:
        return None
    value = claims.get("tid")
    return value if isinstance(value, str) and value else None


async def get_workiq_login_hint(profile: WorkIQOAuthProfile = PREVIEW_PROFILE) -> str | None:
    """The last signed-in WorkIQ account name, or ``None`` if never captured."""
    async with SessionLocal() as session:
        row = await session.get(AppSetting, profile.login_hint_key)
    if row is None or not row.value:
        return None
    try:
        hint = json.loads(row.value)
    except (ValueError, TypeError):
        return None
    return hint if isinstance(hint, str) and hint else None


async def _stored_token_expiry(
    token: OAuthToken, profile: WorkIQOAuthProfile = PREVIEW_PROFILE
) -> datetime | None:
    """Absolute expiry of the stored tokens, or ``None`` when it can't be known.

    Combines the ``issued_at`` stamp written by :meth:`DbTokenStorage.set_tokens`
    with the token's relative ``expires_in``. Returns ``None`` for legacy tokens
    saved before the stamp existed or tokens that omit ``expires_in`` — callers
    then fall back to a conservative time-to-live.
    """
    if token.expires_in is None:
        return None
    async with SessionLocal() as session:
        row = await session.get(AppSetting, profile.issued_at_key)
    if row is None or not row.value:
        return None
    # New rows are JSON-encoded; tolerate legacy rows saved as a raw ISO string.
    try:
        stamp = json.loads(row.value)
    except (ValueError, TypeError):
        stamp = row.value
    try:
        issued = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None
    return issued + timedelta(seconds=token.expires_in)


def _augment_authorization_url(url: str, *, login_hint: str | None, prompt: str | None) -> str:
    """Splice ``login_hint``/``prompt`` into the SDK-built Entra auth URL.

    The MCP SDK constructs the authorization URL itself and doesn't expose these
    OAuth parameters, so we add them to the finished URL before it's opened.
    Existing query params are never clobbered — if the SDK ever sets one, it
    wins — and empty values are skipped. ``login_hint`` pre-selects the account;
    ``prompt=none`` requests a silent (no-UI) authorization.
    """
    if not login_hint and not prompt:
        return url
    split = urlsplit(url)
    params = dict(parse_qsl(split.query, keep_blank_values=True))
    if login_hint and "login_hint" not in params:
        params["login_hint"] = login_hint
    if prompt and "prompt" not in params:
        params["prompt"] = prompt
    return urlunsplit(split._replace(query=urlencode(params)))


async def _redirect_handler(
    authorization_url: str,
    *,
    open_system_browser: bool,
    profile: WorkIQOAuthProfile = PREVIEW_PROFILE,
    login_hint: str | None = None,
    prompt: str | None = None,
    publish_url: bool = True,
) -> None:
    """Surface the WorkIQ authorization URL for sign-in.

    By default publishes the URL over the event bus so the window that started
    the sign-in can navigate a script-opened popup (or the invisible silent
    frame) to it — that popup's loopback callback can then close itself, whereas
    a tab opened out-of-band can't. ``open_system_browser`` *additionally* opens
    the OS default browser, used both when the SPA couldn't open a popup and,
    for the hands-free auto flow, to **self-trigger** the visible interactive
    sign-in with no click. ``publish_url`` is turned off for that self-triggered
    interactive pass so the still-attached silent frame isn't steered into the
    interactive URL (which would race the OS browser for the single loopback
    port). ``login_hint``/``prompt`` are spliced into the URL to pre-select the
    account and (for the silent pass) request a no-UI authorization.
    """
    authorization_url = _augment_authorization_url(
        authorization_url, login_hint=login_hint, prompt=prompt
    )
    logger.info("%s: authorization URL ready; surfacing sign-in", profile.server)
    if publish_url:
        with contextlib.suppress(Exception):
            await publish_mcp_auth_url(profile.server, authorization_url)
    if not open_system_browser:
        return
    try:
        webbrowser.open(authorization_url)
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.warning("%s: could not open a browser automatically: %s", profile.server, exc)


def _make_redirect_handler(
    interactive: bool,
    *,
    profile: WorkIQOAuthProfile = PREVIEW_PROFILE,
    open_system_browser: bool = True,
    login_hint: str | None = None,
    prompt: str | None = None,
    publish_url: bool = True,
) -> Callable[[str], Awaitable[None]]:
    """Build the SDK ``redirect_handler`` for an interactive or background provider.

    The SDK only reaches the redirect handler on a *full* authorization-code
    grant (a 401 the silent refresh couldn't resolve). For background providers
    we refuse to open a browser there and raise :class:`WorkIQAuthRequiredError`
    so the connect fails fast instead of blocking on a sign-in nobody is driving.
    ``open_system_browser`` (interactive only) toggles the OS-browser fallback:
    the SPA sets it off once it has opened its own script-openable popup, and the
    hands-free auto flow sets it on to self-trigger the visible interactive
    prompt. ``publish_url``/``login_hint``/``prompt`` are forwarded to
    :func:`_redirect_handler`.
    """

    async def _handler(authorization_url: str) -> None:
        if not interactive:
            raise WorkIQAuthRequiredError(
                f"{profile.label} needs you to sign in again to continue."
            )
        await _redirect_handler(
            authorization_url,
            open_system_browser=open_system_browser,
            profile=profile,
            login_hint=login_hint,
            prompt=prompt,
            publish_url=publish_url,
        )

    return _handler


# Seconds the success page waits before trying to close itself.
_CALLBACK_AUTOCLOSE_SECONDS = 2


def _render_callback_page(*, status: str, title: str, message: str, label: str = "WorkIQ") -> str:
    """Build the styled HTML shown in the loopback OAuth callback tab.

    The page mirrors Precursor's look (theme tokens, Inter font, dark-mode via
    ``prefers-color-scheme``) so it feels like part of the app, and — on
    success — counts down and closes the tab automatically so the user isn't
    left staring at a stray browser tab once they're connected.
    """
    auto_close = status == "success"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Precursor — {label} sign-in</title>
<style>
  :root {{
    --bg: #ffffff; --surface: #f7f7f8; --border: #e5e7eb;
    --text: #111827; --muted: #6b7280; --accent: #2563eb;
    --ok: #16a34a; --err: #dc2626;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0b0d10; --surface: #15181d; --border: #2a2f37;
      --text: #e6e8eb; --muted: #8a93a0; --accent: #60a5fa;
      --ok: #34d399; --err: #f87171;
    }}
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; margin: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: "Inter", system-ui, -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    max-width: 420px; width: 100%;
    padding: 32px;
    text-align: center;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
  }}
  .badge {{
    width: 56px; height: 56px; margin: 0 auto 20px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: color-mix(in srgb, var(--accent) 14%, transparent);
  }}
  .badge svg {{ width: 30px; height: 30px; }}
  .badge.success svg {{ color: var(--ok); }}
  .badge.error svg {{ color: var(--err); }}
  .badge.pending svg {{ color: var(--accent); }}
  .brand {{
    font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 6px;
  }}
  h1 {{ font-size: 1.25rem; font-weight: 600; margin: 0 0 10px; }}
  p {{ color: var(--muted); line-height: 1.5; margin: 0; font-size: 0.95rem; }}
  .countdown {{ margin-top: 18px; font-size: 0.85rem; color: var(--muted); min-height: 1.2em; }}
  .countdown b {{ color: var(--text); font-variant-numeric: tabular-nums; }}
</style>
</head>
<body>
  <main class="card">
    <div class="badge {status}">
      {_CALLBACK_ICONS[status]}
    </div>
    <div class="brand">Precursor</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <div class="countdown" id="countdown"></div>
  </main>
  <script>
    (function () {{
      var autoClose = {str(auto_close).lower()};
      var pending = {str(status == "pending").lower()};
      var el = document.getElementById("countdown");
      if (!autoClose) {{
        // ``pending`` means a silent attempt fell through and Precursor is about
        // to re-drive this window to the interactive prompt — don't tell the
        // user to close it.
        if (el) el.textContent = pending ? "" : "You can close this tab.";
        return;
      }}
      var remaining = {_CALLBACK_AUTOCLOSE_SECONDS};
      function render() {{
        if (el) el.innerHTML = "Closing this tab in <b>" + remaining + "</b>s\u2026";
      }}
      render();
      var timer = setInterval(function () {{
        remaining -= 1;
        if (remaining <= 0) {{
          clearInterval(timer);
          window.close();
          if (el) el.textContent = "You can close this tab and return to Precursor.";
          return;
        }}
        render();
      }}, 1000);
    }})();
  </script>
</body>
</html>"""


_CALLBACK_ICONS = {
    "success": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M20 6 9 17l-5-5" /></svg>'
    ),
    "error": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10" /><path d="m15 9-6 6" />'
        '<path d="m9 9 6 6" /></svg>'
    ),
    "pending": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10" /><path d="M12 7v5l3 2" /></svg>'
    ),
}


def _make_callback_handler(
    timeout: float = _CALLBACK_TIMEOUT_SECONDS,
    *,
    silent: bool = False,
    profile: WorkIQOAuthProfile = PREVIEW_PROFILE,
) -> Callable[[], Awaitable[tuple[str, str | None]]]:
    """Build the SDK ``callback_handler`` bound to a specific wait ``timeout``.

    The silent auto re-auth uses a much shorter timeout than the interactive
    flow (see :data:`_SILENT_REAUTH_CALLBACK_TIMEOUT_SECONDS`) so a frame that
    can't complete silently falls back to the visible prompt quickly instead of
    parking the loopback for minutes.

    ``silent`` marks a ``prompt=none`` pass: when its loopback never fires (the
    invisible frame couldn't complete without UI — framing / third-party cookies
    blocked, or no live SSO), the timeout is semantically "interaction required",
    so we raise :class:`WorkIQInteractionRequiredError` (which the caller handles
    by falling back to the visible prompt and which :class:`_SuppressExpectedAuthError`
    keeps out of the logs). An interactive (visible-prompt) loopback that times
    out means the user never completed the sign-in; we raise the dedicated
    :class:`WorkIQAuthTimeoutError` — also suppressed and handled benignly — rather
    than a loud, opaque ``RuntimeError``.
    """

    async def _callback_handler() -> tuple[str, str | None]:
        """Run a one-shot loopback server and return ``(auth_code, state)``.

        Listens on ``127.0.0.1:<profile.redirect_port>`` for the single OAuth
        redirect, parses ``code``/``state`` off the query string, replies with a
        styled success page that auto-closes the tab, and resolves.
        """
        loop = asyncio.get_running_loop()
        result: asyncio.Future[tuple[str, str | None]] = loop.create_future()

        async def _on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = await reader.readline()
                target = ""
                parts = request_line.decode("latin-1").split(" ")
                if len(parts) >= 2:
                    target = parts[1]
                query = parse_qs(urlsplit(target).query)
                code = query.get("code", [""])[0]
                state = query.get("state", [None])[0]
                error = query.get("error", [None])[0]

                # Ignore stray connections that aren't the OAuth redirect —
                # favicon fetches, browser/OS connectivity probes, pre-connects,
                # or a manual hit on the loopback. They carry neither ``code``
                # nor ``error``; resolving the future on them would abort a
                # sign-in that hasn't actually redirected back yet with a
                # spurious "No authorization code" failure. Answer benignly and
                # keep listening for the genuine redirect (or the outer timeout).
                if not code and not error:
                    with contextlib.suppress(Exception):
                        writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
                        await writer.drain()
                    return

                interaction_required = error in _INTERACTION_REQUIRED_ERRORS

                if interaction_required:
                    # A silent ``prompt=none`` pass Precursor will retry with a
                    # visible prompt — show a calm "one moment" page, not a failure.
                    body = _render_callback_page(
                        status="pending",
                        title="Finishing sign-in…",
                        message=f"Completing your {profile.label} sign-in — one moment.",
                        label=profile.label,
                    )
                elif error:
                    body = _render_callback_page(
                        status="error",
                        title="Sign-in failed",
                        message=f"{profile.label} couldn't complete the sign-in ({error}).",
                        label=profile.label,
                    )
                elif code:
                    body = _render_callback_page(
                        status="success",
                        title="You're connected",
                        message=f"{profile.label} sign-in is complete.",
                        label=profile.label,
                    )
                else:
                    body = _render_callback_page(
                        status="error",
                        title="Sign-in incomplete",
                        message=f"No authorization code was received from {profile.label}.",
                        label=profile.label,
                    )

                payload = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body.encode('utf-8'))}\r\n"
                    "Connection: close\r\n\r\n"
                    f"{body}"
                )
                writer.write(payload.encode("utf-8"))
                await writer.drain()

                if not result.done():
                    if code:
                        result.set_result((code, state))
                    elif interaction_required:
                        result.set_exception(WorkIQInteractionRequiredError(error))
                    else:
                        result.set_exception(
                            RuntimeError(error or "No authorization code in OAuth callback")
                        )
            except Exception as exc:  # pragma: no cover - defensive
                if not result.done():
                    result.set_exception(exc)
            finally:
                with contextlib.suppress(Exception):
                    writer.close()

        try:
            server = await asyncio.start_server(
                _on_connect, host="127.0.0.1", port=profile.redirect_port
            )
        except OSError as exc:
            # Lost a TOCTOU race with another process (or another Precursor
            # window) for the fixed loopback port. Surface the same clear,
            # typed error the up-front preflight raises rather than a generic
            # transport failure.
            if exc.errno in (errno.EADDRINUSE, errno.EADDRNOTAVAIL, errno.EACCES):
                raise WorkIQAuthPortBusyError(_port_busy_message(profile)) from exc
            raise
        try:
            async with server:
                cancel_event = _active_signin_cancels.get(profile.server)
                if cancel_event is None:
                    # No cancel channel (e.g. a unit test drives the handler
                    # directly) — preserve the plain timed wait.
                    return await asyncio.wait_for(result, timeout=timeout)
                cancel_wait = asyncio.ensure_future(cancel_event.wait())
                try:
                    waiters: set[asyncio.Future[Any]] = {
                        cast("asyncio.Future[Any]", result),
                        cast("asyncio.Future[Any]", cancel_wait),
                    }
                    done, _pending = await asyncio.wait(
                        waiters,
                        timeout=timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    cancel_wait.cancel()
                if result.done():
                    # A genuine redirect arrived (possibly alongside a cancel) —
                    # honour it and let its stored value/exception surface.
                    return result.result()
                if not done:
                    raise TimeoutError
                # The user closed the popup before the redirect: abort cleanly so
                # the loopback releases the fixed port instead of squatting it.
                raise WorkIQAuthCancelledError(f"{profile.label} sign-in was cancelled.")
        except TimeoutError as exc:
            if silent:
                # A silent (``prompt=none``) pass whose loopback never fired: the
                # invisible frame couldn't complete the sign-in without UI. Treat
                # it exactly like Entra's ``interaction_required`` so the caller
                # falls back to the visible prompt — and so the SDK's ERROR
                # traceback for it is dropped by ``_SuppressExpectedAuthError``.
                raise WorkIQInteractionRequiredError(
                    f"{profile.label} silent sign-in timed out; interaction required."
                ) from exc
            # A visible interactive loopback that never fired: the user walked
            # away or closed the tab without the SPA's proactive cancel firing.
            # That's a benign, expected "didn't finish" — a dedicated type the
            # log filter suppresses and the caller re-surfaces as the manual
            # sign-in banner, not an opaque gateway failure.
            raise WorkIQAuthTimeoutError(
                f"Timed out waiting for the {profile.label} sign-in to complete."
            ) from exc

    return _callback_handler


def build_oauth_provider(
    *,
    profile: WorkIQOAuthProfile = PREVIEW_PROFILE,
    interactive: bool = False,
    open_system_browser: bool = True,
    login_hint: str | None = None,
    prompt: str | None = None,
    callback_timeout: float | None = None,
    publish_url: bool = True,
) -> OAuthClientProvider:
    """Build the OAuth provider used as the ``httpx.Auth`` for the HTTP transport.

    ``interactive=False`` (the default, used for the warm pool / catalog probes /
    chat turns) silently refreshes tokens when possible but refuses to launch a
    browser sign-in, surfacing :class:`WorkIQAuthRequiredError` instead. The
    interactive variant — used only by :func:`reauthenticate_workiq` on an
    explicit user action — surfaces the authorization URL and waits for the
    loopback callback. ``open_system_browser`` (interactive only) toggles the
    OS-browser fallback; the SPA turns it off when it drives its own popup, and
    the hands-free auto flow turns it on to self-trigger the visible interactive
    prompt. ``publish_url`` (on by default) can be turned off so the interactive
    URL isn't surfaced to a still-attached silent frame (auto flow).
    ``login_hint`` pre-selects the Entra account and ``prompt`` (e.g. ``"none"``
    for the silent pass) is forwarded onto the authorization request.
    ``callback_timeout`` caps how long the loopback waits for the redirect —
    short for the hands-free silent auto re-auth, long for interactive.
    """
    client_metadata = OAuthClientMetadata(
        redirect_uris=[profile.redirect_uri],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name=profile.client_name,
    )
    return OAuthClientProvider(
        server_url=profile.url,
        client_metadata=client_metadata,
        storage=DbTokenStorage(profile),
        redirect_handler=_make_redirect_handler(
            interactive,
            profile=profile,
            open_system_browser=open_system_browser,
            login_hint=login_hint,
            prompt=prompt,
            publish_url=publish_url,
        ),
        callback_handler=_make_callback_handler(
            callback_timeout if callback_timeout is not None else _CALLBACK_TIMEOUT_SECONDS,
            silent=prompt == "none",
            profile=profile,
        ),
    )


async def resolve_workiq_bearer_token(
    profile: WorkIQOAuthProfile = PREVIEW_PROFILE,
) -> tuple[str, datetime | None] | None:
    """Resolve a current WorkIQ access token plus its expiry, or ``None``.

    The Copilot SDK's HTTP MCP config only accepts *static* headers — it can't
    drive an OAuth ``httpx.Auth`` the way the in-app client does. To let an agent
    reach hosted WorkIQ we therefore have to hand it a concrete bearer token.

    We open a one-shot, non-interactive session first: that lets the OAuth
    provider silently refresh an expired access token and persist the fresh one
    to :class:`DbTokenStorage` before we read it back. Returns ``None`` (so the
    caller can simply skip attaching WorkIQ) when there are no stored tokens or
    the silent refresh needs an interactive sign-in. On success returns
    ``(access_token, expires_at)``; ``expires_at`` is ``None`` when the lifetime
    can't be determined (legacy token / no ``expires_in``).
    """
    storage = DbTokenStorage(profile)
    if await storage.get_tokens() is None:
        return None
    try:
        provider = build_oauth_provider(profile=profile, interactive=False)
        async with (
            streamablehttp_client(profile.url, auth=provider) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
    except Exception as exc:  # pragma: no cover - network/transport dependent
        # The SDK's streamable-http transport runs inside an anyio task group, so
        # our non-interactive redirect handler's ``WorkIQAuthRequiredError`` comes
        # back wrapped in a ``BaseExceptionGroup`` ("unhandled errors in a
        # TaskGroup"). Unwrap it: a genuine sign-in requirement means the stored
        # tokens are dead, so return None (skip attaching WorkIQ) rather than
        # logging a misleading transport failure and handing the agent an expired
        # bearer that would just 401 and re-trigger the sign-in prompt.
        from precursor.backend.services.mcp.client import _find_in_exception

        if _find_in_exception(exc, WorkIQAuthRequiredError) is not None:
            return None
        # A transient connect failure shouldn't strand the agent: fall back to
        # whatever token we already have stored.
        logger.warning("WorkIQ token refresh for agent attach failed: %s", exc)
    tokens = await storage.get_tokens()
    if tokens is None:
        return None
    return tokens.access_token, await _stored_token_expiry(tokens, profile)


async def _run_signin(provider: OAuthClientProvider, profile: WorkIQOAuthProfile) -> None:
    """Open a throwaway hosted WorkIQ session purely to drive the OAuth grant."""
    async with (
        streamablehttp_client(profile.url, auth=provider) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()


def _interactive_prompt(login_hint: str | None) -> str | None:
    """The ``prompt`` for a *visible* authorization, given what we know.

    With a ``login_hint`` we let Entra decide (an existing session for that
    account signs straight through). Without one, force the account picker:
    a hintless authorization against a browser holding several identities can
    otherwise fail outright with AADSTS16000 instead of asking who you are.
    """
    return None if login_hint else "select_account"


async def _try_silent_reauth(
    *,
    profile: WorkIQOAuthProfile,
    login_hint: str | None,
    open_system_browser: bool,
    callback_timeout: float | None = None,
) -> bool:
    """Attempt a no-UI (``prompt=none``) authorization.

    Returns ``True`` when it completed without a visible prompt (the browser
    still held a live Entra SSO session), or ``False`` when Entra reported that
    interaction is required so the caller should fall back to the visible prompt.
    Any other failure propagates. ``callback_timeout`` bounds the loopback wait —
    the hands-free auto re-auth passes a short one so a frame that can't complete
    silently gives up quickly.
    """
    provider = build_oauth_provider(
        profile=profile,
        interactive=True,
        open_system_browser=open_system_browser,
        login_hint=login_hint,
        prompt="none",
        callback_timeout=callback_timeout,
    )
    try:
        await _run_signin(provider, profile)
    except Exception as exc:
        # The streamable-http transport wraps callback errors in a
        # ``BaseExceptionGroup``; unwrap to spot the deliberate "needs UI" signal.
        from precursor.backend.services.mcp.client import _find_in_exception

        if _find_in_exception(exc, WorkIQInteractionRequiredError) is not None:
            logger.info("%s: silent re-auth needs interaction; prompting.", profile.server)
            return False
        raise
    logger.info("%s: silent re-auth succeeded without a prompt.", profile.server)
    return True


async def reauthenticate_workiq(
    *,
    profile: WorkIQOAuthProfile = PREVIEW_PROFILE,
    open_system_browser: bool = True,
    silent_only: bool = False,
    auto: bool = False,
) -> bool:
    """Run the browser OAuth flow and persist fresh WorkIQ tokens.

    Forgets any stored tokens, then drives a throwaway hosted session to obtain
    new ones. To minimize interruption we first pre-select the last account via
    ``login_hint`` and — when :attr:`Settings.workiq_silent_reauth_enabled` is on
    — attempt a silent ``prompt=none`` pass that completes with no clicks if the
    browser still holds a live Entra SSO session; only if Entra reports that
    interaction is required do we fall back to the visible interactive prompt.
    The same script-opened popup is reused for both passes. Serialized via
    :func:`_lock_for` so two triggers can't fight over the redirect port.

    ``open_system_browser`` toggles the OS-browser fallback: the SPA passes it
    off once it has opened its own script-openable popup (so we don't double up
    with a stray tab), on when it couldn't (popup blocked / no live window).

    ``silent_only`` runs a *silent-only* pass: it attempts *only* the no-UI
    ``prompt=none`` pass (on a short timeout, never opening an OS browser) and
    never falls back to a visible prompt — the SPA drives the authorization URL
    through an invisible iframe. Returns ``True`` when authenticated, ``False``
    when the silent pass couldn't complete.

    ``auto`` runs the hands-free **self-triggering** re-auth: the silent
    ``prompt=none`` pass first (invisible iframe, short timeout) and, when Entra
    needs interaction, it *self-opens the OS browser* to the visible interactive
    prompt with **no banner click** — the SPA never has to open a popup. The
    interactive URL isn't surfaced over SSE (``publish_url=False``) so the still
    attached silent frame isn't steered into it and made to race the OS browser
    for the loopback port. Returns ``True`` once authenticated, ``False`` (never a
    hard error) when it couldn't complete so the caller surfaces the manual
    sign-in banner as a last resort.

    Raises :class:`WorkIQAuthInProgressError` if a sign-in is already running.
    """
    lock = _lock_for(profile)
    if lock.locked():
        raise WorkIQAuthInProgressError(f"A {profile.label} sign-in is already in progress.")
    async with lock:
        # Pre-select the last account, but read it before clearing tokens.
        login_hint = await get_workiq_login_hint(profile)

        if silent_only:
            # Silent-only: the no-UI pass, on a short timeout, with no OS browser
            # fallback (the SPA drives an invisible iframe). Any failure — port
            # busy, interaction required, framing/cookies blocked, or timeout —
            # just means "couldn't complete silently", never a hard error.
            # Preflight the loopback port first so a busy port doesn't needlessly
            # clear the still-usable stored tokens.
            try:
                _assert_loopback_port_available(profile)
            except WorkIQAuthPortBusyError as exc:
                logger.info("%s: silent auto re-auth skipped: %s", profile.server, exc)
                return False
            # Drop stale tokens so the flow always re-runs the grant (the retained
            # login_hint still lets the user pick another account in the prompt).
            await clear_workiq_oauth_tokens(profile)
            if not login_hint:
                # Nothing to disambiguate with: Entra answers a hintless
                # ``prompt=none`` against a browser holding several identities
                # with AADSTS16000 — a rendered error page, not a redirect — so
                # the loopback would just hang. Report "can't do it silently".
                logger.info("%s: no known account; silent auto re-auth skipped.", profile.server)
                return False
            try:
                return await _try_silent_reauth(
                    profile=profile,
                    login_hint=login_hint,
                    open_system_browser=False,
                    callback_timeout=_SILENT_REAUTH_CALLBACK_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.info("%s: silent auto re-auth could not complete: %s", profile.server, exc)
                return False

        if auto:
            # Hands-free self-triggering re-auth: prefer the invisible silent pass,
            # but when Entra needs a human, self-open the OS browser to the visible
            # prompt (no banner click). Preflight the port first so a busy port
            # (another window signing in) defers cleanly to the manual banner
            # without destroying still-usable tokens.
            try:
                _assert_loopback_port_available(profile)
            except WorkIQAuthPortBusyError as exc:
                logger.info("%s: auto re-auth skipped: %s", profile.server, exc)
                return False
            await clear_workiq_oauth_tokens(profile)
            _active_signin_cancels[profile.server] = asyncio.Event()
            try:
                if (
                    get_settings().workiq_silent_reauth_enabled
                    and login_hint
                    and await _try_silent_reauth(
                        profile=profile,
                        login_hint=login_hint,
                        open_system_browser=False,
                        callback_timeout=_SILENT_REAUTH_CALLBACK_TIMEOUT_SECONDS,
                    )
                ):
                    return True
                # Silent pass needs a human — self-trigger the visible prompt via
                # the OS browser (no popup gesture). Don't publish the URL: the
                # silent frame is still attached and would otherwise race the OS
                # browser for the single loopback port.
                provider = build_oauth_provider(
                    profile=profile,
                    interactive=True,
                    open_system_browser=True,
                    login_hint=login_hint,
                    prompt=_interactive_prompt(login_hint),
                    publish_url=False,
                )
                await _run_signin(provider, profile)
                return True
            except Exception as exc:
                logger.info("%s: auto re-auth could not complete: %s", profile.server, exc)
                return False
            finally:
                _active_signin_cancels.pop(profile.server, None)

        # Interactive: fail fast — before clearing tokens or driving the browser
        # flow — when another process already owns the fixed loopback port, so
        # the UI shows a clear error instead of stranding "Signing in…" until the
        # callback times out (and without destroying a still-usable session).
        _assert_loopback_port_available(profile)
        # Drop stale tokens so the flow always re-runs the grant (the retained
        # login_hint still lets the user pick another account in the prompt).
        await clear_workiq_oauth_tokens(profile)

        # Arm the cancel channel so the SPA can abort this sign-in (freeing the
        # loopback port immediately) when its popup is closed without finishing.
        _active_signin_cancels[profile.server] = asyncio.Event()
        try:
            if (
                get_settings().workiq_silent_reauth_enabled
                and login_hint
                and await _try_silent_reauth(
                    profile=profile,
                    login_hint=login_hint,
                    open_system_browser=open_system_browser,
                )
            ):
                return True

            provider = build_oauth_provider(
                profile=profile,
                interactive=True,
                open_system_browser=open_system_browser,
                login_hint=login_hint,
                prompt=_interactive_prompt(login_hint),
            )
            await _run_signin(provider, profile)
            return True
        finally:
            _active_signin_cancels.pop(profile.server, None)
