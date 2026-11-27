"""Remote access to the UI through a forwarding tunnel (issue #521).

Precursor has no authentication of its own: a request that reaches the port is
trusted. That stays true for requests made on this machine. A request that came
through a tunnel or proxy is *remote*: once remote access is enabled it needs a
tunnel session (a cookie from the QR pairing), and the features marked
local-only never answer it. See ``docs/adr/0002-remote-ui-access.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from precursor.backend.services.remote_access.classify import is_remote

__all__ = ["is_remote", "through_enforced_tunnel"]


async def through_enforced_tunnel(scope: Mapping[str, Any]) -> bool:
    """A remote request while remote access is on: a paired phone.

    With remote access off, remote requests keep today's behaviour (a LAN
    bind, the user's own reverse proxy), so only this case is restricted.
    """
    if not is_remote(scope):
        return False
    from precursor.backend.db import SessionLocal
    from precursor.backend.services.remote_access import sessions as store

    async with SessionLocal() as db:
        return (await store.load(db)).enabled
