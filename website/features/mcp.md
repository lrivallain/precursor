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
| `fetch` | Fetch and read web content, and make arbitrary HTTP requests. |
| `playwright` | Drive a real Chromium — navigate, read the rendered DOM/text, screenshot. Uses a **persistent profile** so an interactive sign-in reaches **authenticated** pages (see below). |
| `workspace-fs` | Sandboxed file operations inside a [workspace](/features/workspaces). |
| `drawio` | Author native `.drawio` diagrams into a workspace, with server-side layout (see below). |
| `cmd-runner` | Run bash / python / node in a [Docker jail](/features/command-runner). |
| `workiq` | Microsoft 365 (mail, calendar, …) — read-only locally, or full read/write via the hosted preview. |
| `workiq-teams` | Microsoft Teams via [Agent 365](#agent-365-workiq-teams-and-workiq-user) — chats, channels, messages, presence. |
| `workiq-user` | Directory and people lookups via Agent 365 — profiles, managers, direct reports. |
| `precursor` | Precursor's *own* data (see below). |

You can also add **your own** servers (stdio or streamable-HTTP). A
host-dependency **preflight** gates enabling a server — `cmd-runner` needs Docker
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
[`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) to drive a real
browser: the model can **navigate**, read the **rendered** DOM/text (not raw
HTML), and take **screenshots**. This is what `fetch` can't do — it does raw HTTP
with no browser and no session, so a JS-rendered or login-gated page comes back
empty or as a sign-in redirect.

Two things make **authenticated** endpoints reachable:

- **Microsoft Edge is the default channel**, so the browser can ride the
  **corporate Edge SSO / WAM broker** that signs a managed machine in to
  Microsoft sites with little or no interaction. Pick another browser in
  **Settings → MCP → Playwright browser** (or `PRECURSOR_PLAYWRIGHT_BROWSER`) on
  machines without Edge.
- **A persistent browser profile.** By default Precursor pins nothing, so
  `@playwright/mcp` uses its own shared, machine-wide profile — meaning a
  sign-in you already onboarded there carries over. The browser opens **headed**,
  so the first time a page needs Entra you sign in once and every later turn
  reuses that session. Set `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` to pin an isolated
  profile instead.

::: warning Trusted, local use
The persistent profile stores a live authenticated session on disk, and headed
sign-in needs a real display. Treat it like the host-mode
[command runner](/features/command-runner): a single-user, trusted-machine
capability, not something to expose on a shared/headless server.
:::

### draw.io — diagrams as files, not pictures

`drawio` writes native **`.drawio`** documents (plain mxGraph XML) into a
[workspace](/features/workspaces) working tree. Because the output is a *file* in
a git-backed tree, a diagram is reviewable in a `git diff` and commit-able from
the Workspace UI — and it stays editable in draw.io, unlike a rendered image. It
is also editable **in Precursor**, via
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

**Real icons, not blank boxes.** draw.io's Azure library is a set of **SVG
images**, *not* `mxgraph.azure2.*` stencils. Models reliably invent the stencil
form, draw.io can't resolve it, and every service degrades to a featureless
rectangle. So Precursor ships a **catalogue of ~700 verified Azure shapes** and
serves it through `search_shapes`; `create_diagram` also resolves free text
(`"azure firewall"`) and **reports what it matched**, so an unresolved shape is
something you get told about rather than discover in the rendered file.

::: tip Colours are for boxes, not icons
`color` tints plain shapes. It's ignored for catalogue icons — painting a fill
over an SVG icon is precisely what flattens it into a coloured square.
:::

**Layout, including nested containers.** Models write plausible mxGraph but pick
poor coordinates, so shapes overlap and edges cross. Describe the *graph*
instead — nodes, edges and **groups** — and the server derives the geometry.
`groups` nest freely via their own `group` field, which is what makes real cloud
topologies expressible (region → VNet → subnet → resource) without dropping to
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

Any field that takes a preset also accepts a **raw mxGraph style**, so the rest
of the draw.io catalogue — AWS, UML, BPMN — stays reachable. Output is
deterministic, so regenerating an unchanged diagram produces an empty `git diff`.
Existing files are never clobbered unless you pass `overwrite=true`.

### Signing in to WorkIQ and Agent 365

`workiq` has a **preview** toggle: off, it runs the local stdio launcher
(read-only `ask`); on, it switches to the hosted, **OAuth-protected** HTTP
endpoint for the full read **and write** surface. Sign-in is a browser flow, and
when one is needed an inline banner surfaces it right in the app — chat, topic,
workspace and agent turns pause and stream an auth prompt rather than failing.

Precursor is built to keep those credentials alive **without interrupting you**:
tokens are refreshed silently before they expire, a lapsed credential is renewed
hands-free where the browser still holds a live session, and when several servers
go stale at once you get **one prompt, not one per credential**. The banner is
the last resort, not the first move.

Idle credentials are allowed to go quiet — a server you enabled months ago and
never call stops being refreshed, and stops nagging — but a genuine lapse is
surfaced proactively rather than discovered as a stalled request.

Every part of that is tunable; see the `PRECURSOR_WORKIQ_*` entries in the
[configuration reference](/reference/configuration#mcp-tool-servers) to disable
automatic re-auth, change the keep-alive window, or restore stricter behaviour.

### Agent 365: `workiq-teams` and `workiq-user`

Microsoft's **Agent 365** platform exposes two more hosted MCP endpoints, and
Precursor ships both as built-ins:

| Server | Covers |
| --- | --- |
| `workiq-teams` | Teams: list/send chat and channel messages, members, presence, files. |
| `workiq-user` | Directory: your profile, other users, managers, direct reports. |

**One sign-in covers both.** They authenticate as the same Entra client against
the same resource, so Precursor caches a single Agent 365 credential — sign in
from either and both come up. The WorkIQ preview is a *different* client and
resource, so it keeps its own token; signing in to Teams never disturbs it.

**They need your Microsoft tenant.** The endpoint URL embeds a tenant **GUID**,
and Entra rejects the `common` / `organizations` aliases there. Precursor
resolves one in order: **Settings → MCP → "Microsoft 365 tenant"**, then
`PRECURSOR_WORKIQ_TENANT_ID`, then **auto-discovery** from a token you already
hold via a hosted WorkIQ sign-in. In practice, if you've signed in to the WorkIQ
preview the two servers configure themselves with nothing to fill in. Until a
tenant is known the entries stay unconfigured and say so.

## As a server — exposing your conversations

Precursor runs a `FastMCP` server named **`precursor`** that exposes its own data
to MCP hosts (VS Code, CLI agents): topics, messages, chats, agents, live
(meeting) sessions, cross-entity search, skills, memory,
[agent state](/features/agents-mode/artifacts-state#durable-state-the-private-scratchpad),
[workflow state](/features/workflows/steps#pipeline-state-what-a-workflow-remembers),
`append_note`, `post_message`, schedules, and reminders.

`append_note` and `post_message` are deliberately different tools. `append_note`
persists the text you hand it and returns — the right call for filing an
already-written briefing into a topic. `post_message` spends a whole generation
replying to it, which is what you want for a question and pure overhead for a
note.

**Search spans every surface** — the same ⌘K palette engine — so a host can find
a topic, chat, agent task or meeting by content and then follow the hit's
`accessor` hint (`get_chat`, `get_agent`, …) to read the full record. Chat, agent
and live hits only appear when their own section is exposed, since their snippets
disclose that content.

**Topics come back with their tree position resolved.** Every topic payload
carries a `path` — the ancestor slugs joined root-first with `/` — so a caller
never has to walk `parent_id` upward with a chain of `get_topic` round trips.

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
