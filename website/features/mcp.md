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
| `playwright` | Drive a real Chromium — navigate, read the rendered DOM/text, screenshot. Uses a **persistent profile** so an interactive sign-in reaches **authenticated** pages (see below). |
| `workspace-fs` | Sandboxed file operations inside a [workspace](/features/workspaces). |
| `cmd-runner` | Run bash / python / node in a [Docker jail](/features/command-runner). |
| `workiq` | Microsoft 365 (mail, calendar, …) — read-only locally, or full read/write via the hosted preview. |
| `precursor` | Precursor's *own* data (see below). |

You can also add **your own** servers (stdio or streamable-HTTP). A host-dependency
**preflight** gates enabling a server — for example, `cmd-runner` needs Docker
when its jail is on, and `playwright` needs Node.js (`npx`) on PATH.

### Playwright — authenticated scraping

`playwright` wraps Microsoft's official
[`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) (launched via
`npx`, like `workiq`) to drive a real browser: the model can **navigate**,
read the **rendered** DOM/text (not raw HTML), and take **screenshots**. This is
what `fetch` can't do — it does raw HTTP with no browser and no session, so a
JS-rendered or login-gated page comes back empty or as a sign-in redirect.

Two things make **authenticated** endpoints (e.g. an internal
`learningplayer.microsoft.com/activity/…/launch` behind Entra) reachable:

**1. Microsoft Edge (the default).** Precursor launches the `msedge` channel, not
bundled Chromium, so the browser can ride the **corporate Edge SSO / WAM broker** —
the same mechanism that lets a managed machine sign in to Microsoft sites with
little or no interaction. (This mirrors the internal CSU cockpit scrapers, which
drive Edge for exactly this reason.) Set `PRECURSOR_PLAYWRIGHT_BROWSER=chromium`
(or `chrome`, `firefox`, `webkit`) on machines without Edge installed.

**2. A persistent browser profile.** By default Precursor pins **nothing**, so
`@playwright/mcp` uses its **own shared, machine-wide profile** (e.g.
`~/Library/Caches/ms-playwright/mcp-msedge-profile` on macOS) — the same one
any other Playwright-MCP tool uses. So if you already onboarded a sign-in there
(via the Copilot CLI's Playwright tool, an earlier run, …), it **carries over**
and you don't sign in again. The browser opens **headed**, so the first time:

1. Enable `playwright` in **Settings → MCP** and ask for the page. Edge opens; if
   the SSO broker can't sign you in silently, the Entra sign-in appears.
2. **Sign in once** — the cookies/session are written to the shared profile.
3. Every later turn (in any chat, topic, or [agent](/features/agents)) reuses that
   profile, so the model reaches the authenticated content without signing in
   again — until the session naturally expires.

Set `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` to a path only if you want to **pin an
isolated profile** for Precursor instead of sharing the default one.

::: warning Trusted, local use
The persistent profile stores a live authenticated session on disk, and headed
sign-in needs a real display. Treat it like the host-mode
[command runner](/features/command-runner): a single-user, trusted-machine
capability, not something to expose on a shared/headless server.
:::

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
