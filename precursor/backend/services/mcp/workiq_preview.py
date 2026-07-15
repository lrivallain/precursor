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
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import webbrowser
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

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


class _SuppressExpectedAuthError(logging.Filter):
    """Drop the SDK's ERROR traceback for an *expected* WorkIQ sign-in prompt.

    The MCP SDK logs ``logger.exception("OAuth flow error")`` for any exception
    raised inside its auth flow, then re-raises. When a background connect hits
    a sign-in it deliberately won't run, we raise :class:`WorkIQAuthRequiredError`
    from the redirect handler on purpose — so that "error" is a normal, handled
    ``needs_auth`` signal, not a failure. We already log it concisely at WARNING
    in the client, so this filter strips the misleading full stack trace.
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
            if isinstance(node, WorkIQAuthRequiredError):
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

_CALLBACK_TIMEOUT_SECONDS = 300.0

# Serializes interactive sign-ins so two triggers can't open competing browser
# flows fighting over the single loopback redirect port.
_reauth_lock = asyncio.Lock()


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

    ``client_info`` is fixed: we always hand the SDK the WorkIQ-published public
    client id so it skips dynamic registration (see ``OAuthClientProvider`` step
    4). Only the issued tokens are persisted, so a successful login survives app
    restarts.
    """

    _client_info = OAuthClientInformationFull(
        client_id=WORKIQ_OAUTH_CLIENT_ID,
        redirect_uris=[WORKIQ_OAUTH_REDIRECT_URI],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="Precursor (WorkIQ preview)",
    )

    async def get_tokens(self) -> OAuthToken | None:
        async with SessionLocal() as session:
            row = await session.get(AppSetting, OAUTH_TOKENS_KEY)
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
        async with SessionLocal() as session:
            row = await session.get(AppSetting, OAUTH_TOKENS_KEY)
            if row is None:
                session.add(AppSetting(key=OAUTH_TOKENS_KEY, value=encoded))
            else:
                row.value = encoded
            issued_row = await session.get(AppSetting, OAUTH_ISSUED_AT_KEY)
            if issued_row is None:
                session.add(AppSetting(key=OAUTH_ISSUED_AT_KEY, value=issued_at))
            else:
                issued_row.value = issued_at
            await session.commit()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        # The client is pre-registered; nothing to persist.
        return None


async def clear_workiq_oauth_tokens() -> None:
    """Forget any stored tokens so the next connect re-runs the browser login."""
    async with SessionLocal() as session:
        for key in (OAUTH_TOKENS_KEY, OAUTH_ISSUED_AT_KEY):
            row = await session.get(AppSetting, key)
            if row is not None:
                await session.delete(row)
        await session.commit()


async def _stored_token_expiry(token: OAuthToken) -> datetime | None:
    """Absolute expiry of the stored tokens, or ``None`` when it can't be known.

    Combines the ``issued_at`` stamp written by :meth:`DbTokenStorage.set_tokens`
    with the token's relative ``expires_in``. Returns ``None`` for legacy tokens
    saved before the stamp existed or tokens that omit ``expires_in`` — callers
    then fall back to a conservative time-to-live.
    """
    if token.expires_in is None:
        return None
    async with SessionLocal() as session:
        row = await session.get(AppSetting, OAUTH_ISSUED_AT_KEY)
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


async def _redirect_handler(authorization_url: str, *, open_system_browser: bool) -> None:
    """Surface the WorkIQ authorization URL for sign-in.

    Always publishes the URL over the event bus so the window that started the
    sign-in can navigate a script-opened popup to it (that popup's loopback
    callback can then close itself — a tab opened out-of-band can't).
    ``open_system_browser`` *additionally* opens the OS default browser as a
    fallback, used when the SPA couldn't open a popup (blocked / no live window).
    """
    logger.info("WorkIQ preview: authorization URL ready; surfacing sign-in")
    with contextlib.suppress(Exception):
        await publish_mcp_auth_url("workiq", authorization_url)
    if not open_system_browser:
        return
    try:
        webbrowser.open(authorization_url)
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.warning("WorkIQ preview: could not open a browser automatically: %s", exc)


def _make_redirect_handler(
    interactive: bool, *, open_system_browser: bool = True
) -> Callable[[str], Awaitable[None]]:
    """Build the SDK ``redirect_handler`` for an interactive or background provider.

    The SDK only reaches the redirect handler on a *full* authorization-code
    grant (a 401 the silent refresh couldn't resolve). For background providers
    we refuse to open a browser there and raise :class:`WorkIQAuthRequiredError`
    so the connect fails fast instead of blocking on a sign-in nobody is driving.
    ``open_system_browser`` (interactive only) toggles the OS-browser fallback:
    the SPA sets it off once it has opened its own script-openable popup.
    """

    async def _handler(authorization_url: str) -> None:
        if not interactive:
            raise WorkIQAuthRequiredError("WorkIQ needs you to sign in again to continue.")
        await _redirect_handler(authorization_url, open_system_browser=open_system_browser)

    return _handler


# Seconds the success page waits before trying to close itself.
_CALLBACK_AUTOCLOSE_SECONDS = 2


def _render_callback_page(*, status: str, title: str, message: str) -> str:
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
<title>Precursor — WorkIQ sign-in</title>
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
      var el = document.getElementById("countdown");
      if (!autoClose) {{
        if (el) el.textContent = "You can close this tab.";
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
}


async def _callback_handler() -> tuple[str, str | None]:
    """Run a one-shot loopback server and return ``(auth_code, state)``.

    Listens on ``127.0.0.1:WORKIQ_OAUTH_REDIRECT_PORT`` for the single OAuth
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

            if error:
                body = _render_callback_page(
                    status="error",
                    title="Sign-in failed",
                    message=f"WorkIQ couldn't complete the sign-in ({error}).",
                )
            elif code:
                body = _render_callback_page(
                    status="success",
                    title="You're connected",
                    message="WorkIQ sign-in is complete.",
                )
            else:
                body = _render_callback_page(
                    status="error",
                    title="Sign-in incomplete",
                    message="No authorization code was received from WorkIQ.",
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

    server = await asyncio.start_server(
        _on_connect, host="127.0.0.1", port=WORKIQ_OAUTH_REDIRECT_PORT
    )
    try:
        async with server:
            return await asyncio.wait_for(result, timeout=_CALLBACK_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise RuntimeError("Timed out waiting for the WorkIQ sign-in to complete.") from exc


def build_oauth_provider(
    *, interactive: bool = False, open_system_browser: bool = True
) -> OAuthClientProvider:
    """Build the OAuth provider used as the ``httpx.Auth`` for the HTTP transport.

    ``interactive=False`` (the default, used for the warm pool / catalog probes /
    chat turns) silently refreshes tokens when possible but refuses to launch a
    browser sign-in, surfacing :class:`WorkIQAuthRequiredError` instead. The
    interactive variant — used only by :func:`reauthenticate_workiq` on an
    explicit user action — surfaces the authorization URL and waits for the
    loopback callback. ``open_system_browser`` (interactive only) toggles the
    OS-browser fallback; the SPA turns it off when it drives its own popup.
    """
    client_metadata = OAuthClientMetadata(
        redirect_uris=[WORKIQ_OAUTH_REDIRECT_URI],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="Precursor (WorkIQ preview)",
    )
    return OAuthClientProvider(
        server_url=WORKIQ_PREVIEW_URL,
        client_metadata=client_metadata,
        storage=DbTokenStorage(),
        redirect_handler=_make_redirect_handler(
            interactive, open_system_browser=open_system_browser
        ),
        callback_handler=_callback_handler,
    )


async def resolve_workiq_bearer_token() -> tuple[str, datetime | None] | None:
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
    storage = DbTokenStorage()
    if await storage.get_tokens() is None:
        return None
    try:
        provider = build_oauth_provider(interactive=False)
        async with (
            streamablehttp_client(WORKIQ_PREVIEW_URL, auth=provider) as (read, write, _),
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
    return tokens.access_token, await _stored_token_expiry(tokens)


async def reauthenticate_workiq(*, open_system_browser: bool = True) -> None:
    """Run the interactive browser OAuth flow and persist fresh WorkIQ tokens.

    Forgets any stored tokens, then opens a throwaway hosted session with an
    *interactive* provider purely to drive the authorization-code grant. The
    issued tokens land in ``AppSetting``, so the next background WorkIQ connect
    picks them up. Serialized via :data:`_reauth_lock` so two triggers can't open
    competing browser flows on the same redirect port.

    ``open_system_browser`` toggles the OS-browser fallback: the SPA passes it
    off once it has opened its own script-openable popup (so we don't double up
    with a stray tab), on when it couldn't (popup blocked / no live window).

    Raises :class:`WorkIQAuthInProgressError` if a sign-in is already running.
    """
    if _reauth_lock.locked():
        raise WorkIQAuthInProgressError("A WorkIQ sign-in is already in progress.")
    async with _reauth_lock:
        # Drop stale tokens so the flow always re-prompts (also lets the user
        # switch accounts) rather than silently reusing a still-valid session.
        await clear_workiq_oauth_tokens()
        provider = build_oauth_provider(interactive=True, open_system_browser=open_system_browser)
        async with (
            streamablehttp_client(WORKIQ_PREVIEW_URL, auth=provider) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
