"""The ASGI gate in front of the whole app for remote requests.

Pure ASGI on purpose: ``BaseHTTPMiddleware`` buffers responses, which would
break the SSE streams. Local requests pass straight through, untouched.
"""

from __future__ import annotations

import json
import time
from http.cookies import CookieError, SimpleCookie
from typing import Any

from precursor.backend.services.openai_proxy import BASE_PATH as OPENAI_BASE_PATH
from precursor.backend.services.remote_access import sessions as store
from precursor.backend.services.remote_access.classify import is_remote

COOKIE_NAME = "__Host-precursor_remote"
PAIR_PATH = "/api/remote/pair"
# What a phone needs before it is paired: the pairing link itself, and what
# lets the home-screen icon install (manifest, icons, service worker).
_OPEN_PATHS = frozenset({PAIR_PATH, "/manifest.webmanifest", "/sw.js"})
# The OpenAI-compatible endpoint checks its own bearer key: its clients are not
# paired browsers, and must get its error shape, not a pairing 401. A phone
# can't read that key (settings redact it), so pairing grants nothing that
# outlives revocation. Webhooks are not exempt: a paired phone can read their
# tokens, so through the tunnel they need a session like everything else.
_OPEN_PREFIXES = ("/icons/", f"{OPENAI_BASE_PATH}/")

_PAIR_PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Precursor</title></head>
<body style="font-family:system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem">
<h1>Pair this device</h1>
<p>This Precursor only opens on devices paired with it. On your computer, open
<strong>Mobile access</strong> from the persona menu and scan the QR code.</p>
</body></html>"""

# A long-lived response re-checks at least this often, so a session that
# expires (30 days unused) stops a stream even without any access change.
_RECHECK_SECONDS = 60.0

Scope = dict[str, Any]


def session_token(scope: Scope) -> str | None:
    for name, value in scope.get("headers") or ():
        if name.lower() != b"cookie":
            continue
        try:
            jar = SimpleCookie(value.decode("latin-1"))
        except CookieError:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None
    return None


def session_cookie(token: str) -> str:
    """The ``Set-Cookie`` value carrying a session token.

    Lax, not Strict: after the tunnel's own sign-in bounces through
    login.microsoftonline.com, the navigation back is cross-site and a Strict
    cookie would be withheld. Lax still keeps it off cross-site POST/PUT/DELETE
    and subresource requests.
    """
    max_age = int(store.SESSION_TTL.total_seconds())
    return f"{COOKIE_NAME}={token}; Max-Age={max_age}; Path=/; Secure; HttpOnly; SameSite=Lax"


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# What the app's own pages send on every call. A page on another site can only
# add a custom header after a CORS preflight, which is never approved for it:
# so a mutation without one is a forged cross-site request. Needed because
# every dev tunnel shares the devtunnels.ms site, so SameSite=Lax lets a page
# on someone else's tunnel send the cookie along.
_APP_HEADER = b"x-client-id"
# sendBeacon can't set headers; this one only cancels a pending sign-in.
_BEACON_SUFFIX = "/reauthenticate/cancel"


def _forged(scope: Scope) -> bool:
    """A mutation that didn't come from one of the app's own pages."""
    if scope.get("method") not in _UNSAFE_METHODS:
        return False
    if scope.get("path", "").endswith(_BEACON_SUFFIX):
        return False
    return not any(
        name.lower() == _APP_HEADER and value for name, value in scope.get("headers") or ()
    )


def _is_open(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


async def _check(scope: Scope, *, touch: bool = True) -> tuple[bool, str | None]:
    """Whether a remote request may go on, and a token to renew its cookie with.

    The token comes back when the session was just touched, so the browser's
    copy slides along with the stored expiry. ``touch=False`` only reads: a
    stream's re-check can't send a cookie any more, so it must not slide the
    stored expiry either.
    """
    from precursor.backend.db import SessionLocal

    async with SessionLocal() as db:
        state = await store.load(db)
    if not state.enabled:
        # Off: today's behaviour (e.g. behind the user's own authenticating
        # reverse proxy). Local-only features still refuse remote requests.
        return True, None
    if _is_open(scope.get("path", "")):
        return True, None
    if _forged(scope):
        return False, None
    token = session_token(scope)
    entry = store.find(state, token)
    if entry is None:
        return False, None
    if not touch or not store.needs_touch(entry):
        return True, None
    # Re-read under the write lock: the device may have been revoked since.
    _state, touched = await store.update(lambda fresh: store.touch(fresh, token))
    return (True, token) if touched is not None else (False, None)


class _GuardedResponse:
    """Wraps a remote request's ``send`` and ``receive`` for as long as it runs.

    Access is checked once on entry, but a response can outlive that: the event
    stream stays open for hours. When the access state changes (a device
    revoked, enforcement switched on), the next chunk re-checks first; a
    request no longer allowed gets its response ended and reads as
    disconnected, so the stream's generator stops instead of delivering more.
    """

    def __init__(
        self, scope: Scope, receive: Any, send: Any, renew: str | None, generation: int
    ) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._cookie = session_cookie(renew).encode("latin-1") if renew else None
        # The generation the entry check started from: a change committed while
        # that check ran is still seen as a change by the first chunk.
        self._generation = generation
        self._checked_at = time.monotonic()
        self._cut = False

    async def _still_allowed(self) -> bool:
        """Re-check on any access change, and at least every minute (expiry)."""
        current = store.generation()
        stale = time.monotonic() - self._checked_at >= _RECHECK_SECONDS
        if current == self._generation and not stale:
            return True
        self._generation = current
        self._checked_at = time.monotonic()
        allowed, _renew = await _check(self._scope, touch=False)
        return allowed

    async def send(self, message: dict[str, Any]) -> None:
        if self._cut:
            return
        kind = message["type"]
        if kind == "http.response.start" and self._cookie is not None:
            message = {
                **message,
                "headers": [*message.get("headers", ()), (b"set-cookie", self._cookie)],
            }
        elif kind == "http.response.body" and not await self._still_allowed():
            self._cut = True
            message = {"type": "http.response.body", "body": b"", "more_body": False}
        await self._send(message)

    async def receive(self) -> dict[str, Any]:
        if self._cut:
            return {"type": "http.disconnect"}
        message: dict[str, Any] = await self._receive()
        return message


async def _refuse(scope: Scope, send: Any) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4401})
        return
    path = scope.get("path", "")
    if path.startswith("/api/"):
        body = json.dumps({"detail": "This device is not paired."}).encode()
        content_type = b"application/json"
    else:
        body, content_type = _PAIR_PAGE, b"text/html; charset=utf-8"
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", content_type), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RemoteAccessGate:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket") or not is_remote(scope):
            await self.app(scope, receive, send)
            return
        generation = store.generation()
        allowed, renew = await _check(scope)
        # A change committed while the check ran: check again on fresh state.
        while allowed and store.generation() != generation:
            generation = store.generation()
            allowed, renewed = await _check(scope)
            renew = renewed or renew
        if not allowed:
            await _refuse(scope, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        guarded = _GuardedResponse(scope, receive, send, renew, generation)
        await self.app(scope, guarded.receive, guarded.send)
