"""Remote access to the UI through a forwarding tunnel (issue #521).

Precursor has no authentication of its own: a request that reaches the port is
trusted. That stays true for requests made on this machine. A request that came
through a tunnel or proxy is *remote*: once remote access is enabled it needs a
tunnel session (a cookie from the QR pairing), and the features marked
local-only never answer it. See ``docs/adr/0002-remote-ui-access.md``.
"""

from __future__ import annotations

from precursor.backend.services.remote_access.classify import is_remote

__all__ = ["is_remote"]
