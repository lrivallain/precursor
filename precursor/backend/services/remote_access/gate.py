"""The ASGI gate in front of the whole app for remote requests.

Pure ASGI on purpose: ``BaseHTTPMiddleware`` buffers responses, which would
break the SSE streams. Local requests pass straight through, untouched.
"""

from __future__ import annotations

import json
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
# paired browsers, and must get its error shape, not a pairing 401.
_OPEN_PREFIXES = ("/icons/", f"{OPENAI_BASE_PATH}/")

_PAIR_PAGE = b"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Precursor</title></head>
<body style="font-family:system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem">
<h1>Pair this device</h1>
<p>This Precursor only opens on devices paired with it. On your computer, open
<strong>Mobile access</strong> from the persona menu and scan the QR code.</p>
</body></html>"""

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


def _is_open(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


async def _check(scope: Scope) -> tuple[bool, str | None]:
    """Whether a remote request may go on, and a token to renew its cookie with.

    The token comes back when the session was just touched, so the browser's
    copy slides along with the stored expiry.
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
    token = session_token(scope)
    entry = store.find(state, token)
    if entry is None:
        return False, None
    if not store.needs_touch(entry):
        return True, None
    # Re-read under the write lock: the device may have been revoked since.
    _state, touched = await store.update(lambda fresh: store.touch(fresh, token))
    return (True, token) if touched is not None else (False, None)


def _renewing(send: Any, token: str) -> Any:
    cookie = session_cookie(token).encode("latin-1")

    async def wrapped(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", ()), (b"set-cookie", cookie)]}
        await send(message)

    return wrapped


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
        allowed, renew = await _check(scope)
        if not allowed:
            await _refuse(scope, send)
            return
        if renew is not None and scope["type"] == "http":
            send = _renewing(send, renew)
        await self.app(scope, receive, send)
