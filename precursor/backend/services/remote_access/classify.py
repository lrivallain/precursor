"""Tell a request made on this machine from one that came from elsewhere."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

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
