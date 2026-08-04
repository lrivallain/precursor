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
| `workiq-teams` | Microsoft Teams via [Agent 365](#agent-365-workiq-teams-and-workiq-user) — chats, channels, messages, presence. |
| `workiq-user` | Directory and people lookups via Agent 365 — profiles, managers, direct reports. |
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

When the refresh token itself ages out, Precursor runs a **hands-free,
self-triggering re-auth** — it prefers automation over interrupting you:

1. It first drives a silent `prompt=none` authorization in an invisible iframe.
   If the browser still holds a live Entra SSO session the token is renewed with
   **zero clicks** and no window ever appears.
2. When that silent pass genuinely needs interaction (or iframe framing /
   third-party cookies block it), Precursor **self-opens your OS browser** to the
   visible sign-in — no banner click, no second prompt. You complete a single
   sign-in and the tab reports it's done, then closes itself right away (an
   automatic sign-in has no countdown to read; only the manual **Sign in** popup
   pauses for a brief "you're connected" beat first).

Only when even the self-triggered sign-in can't run — auto re-auth is off, the
loopback port is busy, or the flow is declined / times out — does the
`McpAuthBanner` surface for a manual **Sign in** as a last resort (a
script-opened popup that reuses the same silent-first flow). Turn the automatic
attempt off with `workiq_auto_reauth_enabled=false` to always require the click.

However the sign-in completes — popup, self-opened OS-browser tab, or silent
pass — the renewal is broadcast to every open window, so any other window still
showing the banner clears it at once instead of prompting for credentials that
are already fresh.

#### One prompt, not one per credential

The built-ins don't all share a credential: the WorkIQ preview and
[Agent 365](#agent-365-workiq-teams-and-workiq-user) are different Entra clients
against different resources, so they hold **separate tokens** that expire on
their own clocks. Left alone that means two banners and two sign-ins.

Precursor collapses them instead. Pending sign-ins are tracked **per credential**
and rendered as **one banner** naming every server involved ("WorkIQ and WorkIQ
Teams need you to sign in…"), and re-auth attempts run **strictly one at a time**
so two flows can never race for the same window.

The single **Sign in** you click covers the first credential — then, the moment it
succeeds, Precursor immediately retries the *others* while your Entra SSO session
is hot. Those retries take the hands-free path (silent `prompt=none` first), so
in the common case the remaining credentials renew with **zero extra clicks** and
the banner disappears on its own. The backend does the same for its side: after
any successful sign-in it re-arms the sign-in prompt for servers still parked on
a stale, *different* credential, so a browser that's already there picks them up
in the same breath. Set `workiq_chain_reauth_enabled=false` to disable that
follow-on pass and renew each credential only when it's independently needed.

::: tip Concurrent sign-ins
Each OAuth-protected server prefers a **fixed** loopback port for its callback —
`12798` for `workiq`, `12799` for `workiq-teams`, `12800` for `workiq-user`. When
that port is already taken — another Precursor window (e.g. a second worktree)
mid-sign-in, or an unrelated app — Precursor now **falls back to a free ephemeral
port** for that one flow instead of refusing to start. Entra ignores the port of a
loopback redirect for public clients (it matches the host and path exactly), so
the fallback is transparent, and several windows can sign in at once.

Closing the sign-in popup still **cancels the flow** immediately. Set
`workiq_loopback_port_fallback=false` to restore the old strict behaviour, where a
busy port fails fast with "port 12798 is already in use — another Precursor window
or app is signing in…" rather than moving.
:::

#### Quiet when you're not using it

The collapse runs on the **backend** too, not just in the banner. When a turn
pauses because servers need authenticating, Precursor reduces that list to one
name **per credential** before it prompts, so two blocked Agent 365 servers ask
you to sign in **once**, not twice. Chat, topic, workspace and scheduled-command
pauses all share that single choke point.

The keep-alive ticker also **backs off for credentials you aren't using**. It
still refreshes a token shortly before it expires so an active session never
breaks mid-turn — but a WorkIQ server you enabled months ago and never call no
longer keeps getting refreshed, and, more to the point, no longer raises a
sign-in prompt when its refresh token finally lapses. Usage is tracked per
credential (calling either Agent 365 server keeps the shared token warm) and the
window defaults to **6 hours**; the clock is seeded at startup, so a freshly
started Precursor keeps everything warm rather than going quiet until your first
tool call. Set `workiq_keepalive_idle_after_seconds=0` to disable the back-off
and keep every signed-in credential warm indefinitely.

### Agent 365: `workiq-teams` and `workiq-user`

Microsoft's **Agent 365** platform exposes two more hosted MCP endpoints, and
Precursor ships both as built-ins:

| Server | Endpoint | Covers |
| --- | --- | --- |
| `workiq-teams` | `…/servers/mcp_TeamsServer` | Teams: list/send chat and channel messages, members, presence, files. |
| `workiq-user` | `…/servers/mcp_MeServer` | Directory: your profile, other users, managers, direct reports. |

They are `streamable_http` and use **the same browser sign-in stack** as the
WorkIQ preview above (silent-first, self-triggering re-auth, keep-alive ticker,
inline banner).

**One sign-in covers both.** They authenticate as the same Entra client against
the same resource, and the consented scope set spans every `McpServers.*`
permission — a token minted for one is accepted verbatim by the other. So
Precursor caches a single Agent 365 credential: sign in from either server and
both come up. The WorkIQ preview is a *different* client **and** a different
resource, so it keeps its own separate token — signing in to Teams never
disturbs your WorkIQ session, and vice versa. When *both* credentials happen to
be stale at once you still only get **one prompt**: the sign-in you complete
chains a hands-free renewal for the other, as described under
[one prompt, not one per credential](#one-prompt-not-one-per-credential).

**They need your Microsoft tenant.** The endpoint URL embeds a tenant **GUID**:

```
https://agent365.svc.cloud.microsoft/agents/tenants/{tenant}/servers/mcp_TeamsServer
```

Entra rejects the `common` / `organizations` aliases there, so Precursor has to
know which tenant to address. It resolves one, in order:

1. **Settings → MCP → “Microsoft 365 tenant”** — paste the GUID.
2. `PRECURSOR_WORKIQ_TENANT_ID` in the environment.
3. **Auto-discovery** — the `tid` claim of a token you already hold from a
   hosted WorkIQ sign-in. In practice, if you've signed in to the WorkIQ preview
   the two servers configure themselves with **nothing to fill in**.

Until a tenant is known the two entries stay unconfigured and say so, rather
than pointing at an unusable URL.

## As a server — exposing your conversations

Precursor runs a `FastMCP` server named **`precursor`** that exposes its own
data to MCP hosts (VS Code, CLI agents): topics, messages, chats, agents, live
(meeting) sessions, cross-entity search, skills, memory (read + write),
`post_message` (runs a full turn), schedules, and reminders.

**Search spans every surface** — the same ⌘K palette engine — so a host can find
a topic, chat, agent task or meeting by content and then follow the hit's
`accessor` hint (`get_chat`, `get_agent`, `get_live_session`, …) to read the full
matching record. Chat/agent/live hits (and their accessor tools) only appear when
their own section is exposed, since their snippets disclose that content.

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
