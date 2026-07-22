---
title: MCP (tools both ways)
---

# MCP — tools both ways

Precursor is **both** an MCP client and an MCP server, with working transports in
each direction. [Model Context Protocol](https://modelcontextprotocol.io) is the
open standard for connecting AI apps to tools and data.

<Screenshot src="/screenshots/mcp-settings.png" alt="The MCP settings tab with toggles for built-in tool servers" caption="Settings → MCP — toggle built-in tool servers per turn and expose your own conversations." />

## As a client — attaching tool servers

Precursor holds a registry of MCP **tool servers**. Each is toggled in
**Settings → MCP**; enabled servers are opened per chat turn and their tools are
advertised to the model, which can then call them inside the
[streamed tool loop](/reference/architecture#request-flow-streamed-chat).

Built-in servers ship in-tree:

| Server | What it does |
| --- | --- |
| `github` | GitHub issue/PR/repo operations. |
| `fetch` | Fetch and read web content. |
| `workspace-fs` | Sandboxed file operations inside a [workspace](/features/workspaces). |
| `cmd-runner` | Run bash / python / node in a [Docker jail](/features/command-runner). |
| `workiq` | Microsoft 365 (mail, calendar, …) — read-only locally, or full read/write via the hosted preview. |
| `precursor` | Precursor's *own* data (see below). |

You can also add **your own** servers (stdio or streamable-HTTP). A host-dependency
**preflight** gates enabling a server — for example, `cmd-runner` needs Docker
when its jail is on.

### WorkIQ preview & OAuth

`workiq` has a **preview** toggle: off, it runs the local stdio launcher
(read-only `ask`); on, it switches to the hosted, **OAuth-protected** HTTP
endpoint for the full read **and write** surface. The sign-in is a browser flow
driven by the SDK's `OAuthClientProvider`, with tokens cached in settings and
**silently refreshed** when possible. When a full sign-in is required, an inline
`McpAuthBanner` surfaces it right in the app — chat, topic, workspace, and agent
turns pause and stream an auth prompt rather than failing. A background keep-alive
ticker refreshes the token before it expires so the hosted session survives
without frequent re-sign-in.

When the refresh token itself ages out, Precursor first tries a **hands-free
re-auth**: it runs the silent `prompt=none` authorization in an invisible iframe,
so if the browser still holds a live Entra SSO session the session is renewed
with **zero clicks** and the banner never appears. Only when a silent pass can't
complete — Entra genuinely needs interaction, or iframe framing / third-party
cookies block it — does the `McpAuthBanner` surface for a manual **Sign in**
(which reuses the same silent-first flow in a real popup). Turn the automatic
attempt off with `workiq_auto_reauth_enabled=false` to always require the click.

::: warning One sign-in at a time per machine
The OAuth callback uses a **fixed** loopback port (`127.0.0.1:12798`, matching
the registered `redirect_uri`), so only one Precursor instance can run the
sign-in at a time. If you have several windows open (e.g. multiple worktrees)
and start a sign-in while another already owns the port, Precursor fails fast
with a clear message ("port 12798 is already in use — another Precursor window
or app is signing in…") **without** disturbing your existing session — finish or
close that other sign-in, then retry. Simply **closing the sign-in popup cancels
the flow** and frees the port immediately, so an abandoned sign-in never blocks
the next one.
:::

## As a server — exposing your conversations

Precursor runs a `FastMCP` server named **`precursor`** that exposes its own
data to MCP hosts (VS Code, CLI agents): topics, messages, search, skills, memory
(read + write), `post_message` (runs a full turn), schedules, and reminders.

Every tool is gated by a per-section **`mcp_expose`** toggle — **off by default**,
because exposing conversation history outbound is opt-in.

Two transports serve the same tools:

- **stdio** — `python -m precursor.backend.services.mcp.precursor_server`; how a
  host launches it as a subprocess.
- **HTTP** — mounted in-process at `/mcp` (streamable-http). **Off by default**,
  **loopback-only**, with a Host-header allowlist (DNS-rebinding protection) and
  no auth — so it never answers on a non-loopback bind.

::: warning Keep MCP-over-HTTP local
The HTTP transport has no authentication and only binds to loopback. Leave it
that way unless you front it with your own authenticating proxy.
:::

See the [architecture reference](/reference/architecture#mcp) for the full
picture of both directions.
