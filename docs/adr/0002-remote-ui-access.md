# ADR-0002: Remote access to the UI through a tunnel

- **Status:** accepted (spec approved on issue #521)
- **Date:** 2026-11-26

## Context

Precursor is single-user and has no authentication: whoever reaches the port
is trusted, which is safe as long as it binds to `127.0.0.1`. Users want to open
their own Precursor from their phone. A forwarding tunnel (Microsoft dev
tunnels) does that without changing the bind: it keeps an outbound connection
to a relay and forwards requests to `127.0.0.1:<port>`.

That breaks the assumption behind the existing loopback guards (the MCP HTTP
endpoint, the plugin installer): they look at the **bind address** and the
`Host` header, and a tunnel satisfies both. Dev tunnels rewrite `Host` to
`localhost` by default.

## Decision

1. **Two kinds of requests.** A request is *remote* when it carries any
   forwarding header (`Forwarded`, `X-Forwarded-For`, `-Host`, `-Proto`) or
   comes from a non-loopback IP. Everything else is *local*. A local browser
   never sends those headers, and adding one by hand only restricts the
   request further: the rule fails closed. (`services/remote_access/classify.py`)
2. **Local requests are unchanged.** No session, no prompt, nothing new.
3. **Remote requests need a tunnel session once remote access is on.** A pure
   ASGI gate in front of the app answers `401` to a remote request without a
   valid `__Host-precursor_remote` cookie, except the pairing link and the
   files the home-screen install needs. With remote access off, the gate
   lets remote requests through as before, so a deployment behind the user's
   own authenticating reverse proxy keeps working.
4. **Pairing by a one-time code.** Only a local request can mint the code
   (128 bits, single use, 30 minutes, burned after 10 wrong guesses). The
   phone exchanges it, through the tunnel, for a session token. Only the
   token's SHA-256 is stored; sessions expire after 30 days unused and can be
   revoked from the PC.
5. **Some features are local-only.** A route marked with
   `Depends(require_local)` answers `404` to remote requests, session or not:
   tunnel control, pairing and paired devices to start with. The MCP HTTP
   endpoint and the plugin installer check `is_remote` in their own guards.
   Anything awkward to drive from a phone can join this list later rather
   than be made to work remotely.
6. **The tunnel is private.** Its relay only lets the account that created it
   through, after an Entra ID sign-in. That is a second barrier, not the only
   one: the app-level session holds even with a misconfigured tunnel.

## Consequences

- Every remote request costs one settings read. Local requests pay nothing.
- The cookie is `SameSite=Lax`, not `Strict`: the relay's sign-in redirects
  back cross-site, and a `Strict` cookie would be withheld on that hop.
  Mutations are `POST`/`PUT`/`DELETE`, which `Lax` keeps off cross-site
  requests.
- Any forwarding proxy is treated as remote, not just dev tunnels; other
  transports can reuse the same boundary.
