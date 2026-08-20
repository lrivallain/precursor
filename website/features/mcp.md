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
| `drawio` | Author native `.drawio` diagrams into a workspace, with server-side layout (see below). |
| `cmd-runner` | Run bash / python / node in a [Docker jail](/features/command-runner). |
| `workiq` | Microsoft 365 (mail, calendar, …) — read-only locally, or full read/write via the hosted preview. |
| `workiq-teams` | Microsoft Teams via [Agent 365](#agent-365-workiq-teams-and-workiq-user) — chats, channels, messages, presence. |
| `workiq-user` | Directory and people lookups via Agent 365 — profiles, managers, direct reports. |
| `precursor` | Precursor's *own* data (see below). |

You can also add **your own** servers (stdio or streamable-HTTP). A host-dependency
**preflight** gates enabling a server — for example, `cmd-runner` needs Docker
when its jail is on, and `playwright` needs Node.js (`npx`) on PATH.

::: tip Narrow the catalogue per workflow step
Enabling a server here offers it to everything. Tool schemas are re-sent on
every turn, so a large registry is a standing context cost. A
[workflow](/features/workflows/steps#picking-which-tool-servers-a-step-gets) step can
name the handful of servers it may use — the rest are never attached to that
step's session.
:::

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
drive Edge for exactly this reason.) Pick a different browser in **Settings → MCP
→ Playwright browser** (or set `PRECURSOR_PLAYWRIGHT_BROWSER=chromium`, `chrome`,
`firefox`, `webkit`) on machines without Edge installed — the Settings choice is a
DB override that wins over the env default and applies without a restart.

::: tip `unknown option '--browser'` — handled automatically
If the `@playwright/mcp` your `npx` resolves is an **older build that predates the
`--browser` flag** (common behind a stale registry mirror), Precursor **detects
this on startup** — it probes the resolved package once and **omits `--browser`
automatically**, so the server launches with its own default browser instead of
failing with `error: unknown option '--browser'`. You don't have to do anything.
If you'd rather force this behavior explicitly, choose **Default** in the browser
selector (or `PRECURSOR_PLAYWRIGHT_BROWSER=default`), which never passes the flag.
:::

**2. A persistent browser profile.** By default Precursor pins **nothing**, so
`@playwright/mcp` uses its **own shared, machine-wide profile** (e.g.
`~/Library/Caches/ms-playwright/mcp-msedge-profile` on macOS) — the same one
any other Playwright-MCP tool uses. So if you already onboarded a sign-in there
(via the Copilot CLI's Playwright tool, an earlier run, …), it **carries over**
and you don't sign in again. The browser opens **headed**, so the first time:

1. Enable `playwright` in **Settings → MCP** and ask for the page. Edge opens; if
   the SSO broker can't sign you in silently, the Entra sign-in appears.
2. **Sign in once** — the cookies/session are written to the shared profile.
3. Every later turn (in any chat, topic, or [agent](/features/agents-mode)) reuses that
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

### draw.io — diagrams as files, not pictures

`drawio` writes native **`.drawio`** documents (plain mxGraph XML) into a
[workspace](/features/workspaces) working tree. Because the output is a *file*
in a git-backed tree, a diagram is reviewable in a `git diff` and commit-able
from the Workspace UI — and it stays editable in draw.io, the desktop app or the
VS Code extension, unlike a rendered image. It is also editable **in Precursor**:
the Files section opens `.drawio` files in an embedded, self-hosted draw.io
editor — see
[Workspaces → Editing diagrams](/features/workspaces#editing-diagrams).

It shares `workspace-fs`'s sandbox: every path goes through `safe_join`, so
nothing outside `workspaces_dir/<slug>` is reachable.

| Tool | What it does |
| --- | --- |
| `list_workspaces` | Find the `workspace_id` to write into. |
| `search_shapes` | Find real product icons (Azure) and their exact style strings. |
| `list_shapes` | The generic shape / colour / edge presets. |
| `create_diagram` | Build a laid-out diagram from nodes, edges and groups. |
| `write_diagram_xml` | Escape hatch — write raw mxGraph XML (validated, wrapper added). |
| `read_diagram` | Read a diagram back to edit and rewrite it. |

Two things exist here because a model left to itself gets them wrong.

#### Real icons, not blank boxes

draw.io's Azure library is a set of **SVG images** (`image=img/lib/azure2/…`),
*not* `shape=mxgraph.azure2.*` stencils. Models reliably invent the stencil form,
draw.io can't resolve it, and every service silently degrades to a featureless
rectangle — which is how an "Azure architecture" ends up as a grid of blue
squares.

So Precursor ships a **catalogue of ~700 verified Azure shapes**, generated from
draw.io's own palette by `scripts/build_drawio_shapes.py`, and serves it through
`search_shapes`:

```text
search_shapes("express route")
→ networking/expressroute-circuits  (Azure ExpressRoute Circuits)
  image;aspect=fixed;html=1;…;image=img/lib/azure2/networking/ExpressRoute_Circuits.svg;
```

Pass the `key` as a node's `shape` and the icon — and its correct aspect ratio —
comes with it. `create_diagram` also resolves free text (`"azure firewall"`) and
**reports what it matched** in `notes`, so an unresolved shape is something you
get told about rather than something you discover in the rendered file.

::: tip Colours are for boxes, not icons
`color` tints plain shapes. It's ignored for catalogue icons — painting a fill
over an SVG icon is precisely what flattens it into a coloured square.
:::

#### Layout, including nested containers

Models write plausible mxGraph but pick poor `x`/`y` coordinates, so shapes
overlap and edges cross. Describe the *graph* instead — nodes, edges and
**groups** — and the server derives the geometry: layered along `direction`, with
a barycenter pass to cut crossings, and every container sized to fit its children
and its own title.

`groups` nest freely via their own `group` field, which is what makes real cloud
topologies expressible — region → VNet → subnet → resource — without dropping to
raw XML:

```text
create_diagram(
  workspace_id = 1,
  path         = "docs/hub-spoke",            # .drawio is appended for you
  direction    = "horizontal",
  groups       = [{"id": "hub",   "label": "Hub VNet — 10.0.0.0/16", "color": "blue"},
                  {"id": "fwsub", "label": "AzureFirewallSubnet",    "group": "hub"},
                  {"id": "spoke", "label": "Spoke 1 — Production",   "color": "green"}],
  nodes        = [{"id": "fw",  "label": "Azure Firewall", "shape": "networking/firewalls", "group": "fwsub"},
                  {"id": "app", "label": "App Tier",       "shape": "compute/vm-scale-sets", "group": "spoke"}],
  edges        = [{"source": "fw", "target": "spoke", "label": "VNet peering"}],
)
```

Edges may join **groups** as well as nodes, and an edge between two nested nodes
also orders the containers they sit in. `direction` sets the flow axis; siblings
with no edges between them are packed along it and wrap instead of running off
the page.

::: tip Diagrams that diff cleanly
Output is deterministic — no timestamps, no random ids. Regenerating an
unchanged diagram produces an empty `git diff` instead of churning the file.
:::

Presets and catalogue keys aside, **any field that takes a preset also accepts a
raw mxGraph style** (anything containing `=`), so the rest of the draw.io
catalogue — AWS, UML, BPMN — stays reachable. Cycles are fine (state machines,
retry loops): the layering cuts the loop rather than rejecting the graph.
Existing files are never clobbered unless you pass `overwrite=true`.

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

#### A credential that can actually be renewed

None of that renewal is possible unless the sign-in *asks* for it, so every
authorization Precursor drives requests **`offline_access`** alongside the
server's own scope. That's what makes Entra return a **refresh token** — without
one the credential is terminal: the moment its access token expires the only way
back is a human at a browser, which is exactly what an unattended agent or a
scheduled workflow doesn't have.

::: warning Existing sign-ins are upgraded once
Tokens stored *before* this was requested have no refresh token and can't gain
one retroactively. Rather than let the keep-alive attempt a renewal that cannot
succeed, Precursor spots the missing refresh token and raises the ordinary
`McpAuthBanner` straight away — so you sign in **once** more and come back with
a renewable credential. Depending on how the WorkIQ preview client is
registered, that sign-in may show a one-off **consent** screen for the new
`offline_access` permission.
:::

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

If a sign-in is ever *orphaned* — its popup closed out of band, the tab reloaded,
or an OS-browser flow (standalone PWA) walked away — the click that would cancel it
never fires, so the credential's flow would otherwise stay parked until it times
out and refuse every retry with "a sign-in is already in progress". Precursor
recovers from that automatically: clicking **Sign in** again **preempts** the stale
flow (it's told to abort, the port frees, and the new attempt takes over), while a
sign-in that's genuinely mid-redirect is left to finish. Reloading or closing the
window also fires a best-effort cancel on unload, so the credential's lock is
released right away instead of being held hostage.
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

#### Surfaced the moment it lapses, not on your next request

Backing off from *idle* credentials used to have a sharp edge: a session you
weren't using could quietly die, and you'd only discover it when your next
request stalled for several seconds while the doomed OAuth handshake ran before
finally raising the banner. That "why is a simple request taking so long?" delay
was the tell that a server needed re-authenticating — surfaced too late, and only
if you thought to check **Settings → MCP**.

Precursor now surfaces the lapse **proactively** instead:

- **The keep-alive raises it for you.** Even for an idle credential, once its
  stored access token has *actually* expired the ticker probes it once. If the
  refresh token is dead, it publishes the same `McpAuthBanner` you'd get from a
  live turn — so you see "…needs you to sign in" without touching Settings. A
  still-refreshable idle session recovers silently, no prompt. This only fires on
  a genuine, network-free-detected expiry, so it never nags for a session that's
  merely resting.
- **The next request is instant, not stalled.** A detected lapse also records a
  verdict in the client pool, so the *first* turn that touches that server
  fast-fails straight to the sign-in prompt instead of paying the multi-second
  handshake against a token that can only fail. Even with the keep-alive off, the
  first failed connect records the verdict so the *second* request is instant.

This trades a little of the anti-nag silence above for not discovering a dead
session the slow way. Set `workiq_keepalive_surface_idle_lapse=false` to keep idle
credentials completely silent until you touch them yourself.

#### Tracing a sign-in

When the banner appears anyway, the useful question is *which* leg gave up — and
both of them hide inside a single request, so there's nothing to see from the
outside. Precursor traces each step of an auth episode to the **browser console**
under `[workiq-auth]`, with elapsed timings:

```text
[workiq-auth] workiq-teams +0ms — notice opened
[workiq-auth] workiq-teams +4ms — hands-free start — POST /reauthenticate?auto=true
[workiq-auth] workiq-teams +812ms — auth url → silent frame (leg ①)
[workiq-auth] workiq-teams +1103ms — leg ① frame load #1  {readable: true, verdict: "likely blocked before load"}
[workiq-auth] workiq-teams +21406ms — hands-free gave up (interaction_required) — banner will show
[workiq-auth] workiq-teams +21407ms — BANNER SHOWN — manual sign-in now required
```

Read it by the timings. Resolving in well under a second with **no `auth url`
line** means the backend never started a leg at all — auto re-auth is off, the
loopback port was busy, or there's no stored account to use as a `login_hint`. An
`auth url` line followed by ~20 s of silence means the silent frame reached Entra
but its loopback never fired. A further long wait after that is the OS-browser
leg — which deliberately does *not* publish its URL, so its absence is the tell —
and that's the one to suspect when your **default browser or profile differs from
the one running Precursor**, since the sign-in then lands somewhere without your
session.

`window.precursorWorkiqAuthTrace()` returns the whole episode as an array if you
want to paste it into an issue; `login_hint`, `state` and `nonce` are never
logged. Set `localStorage['precursor.debug.workiqAuth'] = '0'` to silence the
output (the trace buffer keeps working).

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
[agent state](/features/agents-mode#durable-state-the-private-scratchpad) (an agent's
durable cross-run scratchpad, read + write),
[workflow state](/features/workflows/steps#pipeline-state-what-a-workflow-remembers) (a
pipeline's shared memory, read + write), `post_message` (runs a full turn),
schedules, and reminders.

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
