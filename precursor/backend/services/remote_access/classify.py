"""Tell a request made on this machine from one that came from elsewhere."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from precursor.backend.services.mcp.precursor_server import LOOPBACK_HOSTS

# A forwarding tunnel (dev tunnels) or reverse proxy adds at least one of these.
# A local browser never does, and adding one by hand only makes the request
# remote, i.e. more restricted: the rule fails closed.
FORWARD_HEADERS = frozenset(
    {b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto"}
)


def _client_is_foreign(client: object) -> bool:
    """True for a client address that is an IP and not a loopback one.

    Non-IP clients (a Unix socket, a test transport) are not network peers.
    """
    if not isinstance(client, (tuple, list)) or not client:
        return False
    try:
        address = ipaddress.ip_address(str(client[0]))
    except ValueError:
        return False
    return not address.is_loopback


def is_remote(scope: Mapping[str, Any]) -> bool:
    """Whether an HTTP or websocket ``scope`` came from outside this machine.

    The tunnel forwards to ``127.0.0.1``, so the bind address and the client
    address alone can't tell a tunneled request from a local one: the
    forwarding headers can. A direct peer on another address is remote too.
    """
    headers = scope.get("headers") or ()
    if any(name.lower() in FORWARD_HEADERS for name, _value in headers):
        return True
    return _client_is_foreign(scope.get("client"))


def _header(scope: Mapping[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return str(value.decode("latin-1"))
    return None


def addresses_loopback(scope: Mapping[str, Any]) -> bool:
    """A local request whose ``Host`` names a loopback address.

    The bind address alone is not a boundary: a page on an attacker's domain
    that DNS-rebinds to 127.0.0.1 is same-origin as far as the browser is
    concerned, so no CORS preflight ever runs. Checking the Host header is what
    pins a request to "someone typed localhost". A tunneled request never
    qualifies, whatever it names: tunnels may rewrite ``Host`` to localhost.
    """
    if is_remote(scope):
        return False
    raw = (_header(scope, b"host") or "").strip()
    if not raw:
        return False
    try:
        # Parsed as an authority so the port is dropped and a bracketed IPv6
        # literal ("[::1]:8000") is unwrapped correctly.
        hostname = urlsplit(f"//{raw}").hostname
    except ValueError:
        return False
    return (hostname or "").lower() in LOOPBACK_HOSTS


def origin_is_loopback(scope: Mapping[str, Any]) -> bool:
    """Reject a cross-site caller that reveals itself via ``Origin``.

    A form POST from another site sends ``Origin`` and needs no preflight, so
    this closes the one hole the Host check alone leaves.
    """
    origin = _header(scope, b"origin")
    if not origin:
        return True  # same-origin navigations and non-browser clients
    try:
        host = urlsplit(origin).hostname or ""
    except ValueError:
        return False
    return host.lower() in LOOPBACK_HOSTS


def is_local_caller(scope: Mapping[str, Any]) -> bool:
    """Made on this machine, addressed to localhost, from no foreign page."""
    return addresses_loopback(scope) and origin_is_loopback(scope)
