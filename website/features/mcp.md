---
title: MCP (tools both ways)
---

# MCP — tools both ways

Precursor is both an MCP **client** (attach tool servers to chats, topics and
agents) and an MCP **server** (expose your conversations to VS Code, CLI agents
and other hosts). [Model Context Protocol](https://modelcontextprotocol.io) is
the open standard for connecting AI apps to tools and data. Internals live in
the [MCP reference](/reference/mcp).

<Screenshot src="/screenshots/mcp-settings.png" alt="The MCP settings tab with toggles for built-in tool servers" caption="Settings → MCP — pick which Precursor capabilities to serve (with Select all / Unselect all), then toggle built-in tool servers per turn." />

## As a client — attaching tool servers

Toggle servers in **Settings → MCP servers**. Enabled servers are opened per
turn and their tools are offered to the model inside the
[streamed tool loop](/reference/architecture#request-flow-streamed-chat).

| Server | What it does |
| --- | --- |
| `github` | GitHub issue/PR/repo operations. |
| `fetch` | Fetch web content and make arbitrary HTTP requests. |
| `playwright` | Drive a real browser with a persistent profile, so it can reach **authenticated** pages ([below](#playwright-authenticated-scraping)). Needs Node.js (`npx`). |
| `drawio` | Author native `.drawio` diagrams into a workspace ([below](#draw-io-diagrams-as-files-not-pictures)). |
| `cmd-runner` | Run bash / python / node in a [Docker jail](/features/command-runner). Needs Docker while the jail is on. |
| `workiq` | Microsoft 365 (mail, calendar, …): read-only locally, or read/write via the hosted preview. |
| `workiq-teams`, `workiq-user`, `workiq-planner`, `workiq-word`, `workiq-excel` | Teams, directory, Planner, Word and Excel via [Agent 365](#agent-365-workiq-teams-and-workiq-user). |
| `precursor` | Precursor's own data ([as a server](#as-a-server-exposing-your-conversations)). |

Enabling a server runs a host-dependency **preflight** (Docker, `npx`). With the
`workiq` server off, Microsoft 365 surfaces elsewhere in the app (the meeting
agenda, Teams transcript import) are hidden.

You can add **your own** servers (stdio or streamable HTTP). Plugins can
contribute servers too, such as Kanban's `kanban.board`.

::: tip Narrow the catalogue per workflow step
Enabling a server here offers it to everything. A
[workflow](/features/workflows/steps#picking-which-tool-servers-a-step-gets) step can
name the handful of servers it may use.
:::

### Servers that sign in with Entra ID

For an organisation MCP server that wants a Microsoft Entra ID token instead of
an API key, Precursor can use your **Azure CLI** sign-in:

1. In **Settings → MCP servers**, add an HTTP server and paste its URL. On
   leaving the field (or with **Detect**), Precursor reads the server's OAuth
   metadata and, for an Entra-protected server, sets **Authentication** to
   **Entra ID (Azure CLI)** with the **tenant** and **resource** filled in.
2. Save and enable. Each request carries a token from
   `az account get-access-token --tenant <tenant> --resource <resource>`, cached
   and renewed before it expires. Agent sessions get the same token.

If no token can be had, the server card says what to do: run the
`az login --tenant … --scope <resource>/.default` command shown in the editor,
fix the tenant, or ask the server's owners for access. This only works for
servers that accept the Azure CLI as a client, and the tenant must be the
server's (a token from another tenant is rejected).

HTTP servers that need no headers (for example Microsoft Learn) can also be
attached to agent sessions.

### Tools loaded on demand

Large servers (GitHub, Azure, Teams…) can add hundreds of tools and ~100k tokens
of schemas to every request. By default a chat or topic turn sends only:

- tools the system prompt refers to (e.g. step-plan tools);
- every tool of a server ticked **always load** in **Settings → MCP servers**;
- tools the conversation called recently.

Every other enabled tool is listed **by name only** in the system prompt. The
model calls `search_tools` (exact names or a keyword query) and the matches
become callable from its next step. A small catalogue is sent whole.

Turn **Load tools on demand** off in **Settings → MCP servers** to send every
definition every time. Workflow step assessments always get their full tool
list. To cut the GitHub server's size at the source, trim
`PRECURSOR_GITHUB_MCP_TOOLSETS`.

### Startup and the first prompt

Each server's tool list is stored after a successful connect, so the model is
offered tools before anything reconnects. Shortly after startup, enabled servers
are **warmed in the background**, one at a time; servers that need an
interactive sign-in are skipped. Tune with `PRECURSOR_MCP_WARMUP_*`
([configuration](/reference/configuration#mcp-tool-servers)).

### When a remote server drops mid-turn

A remote 404 (`Session terminated`), any 5xx, or a dropped socket recycles the
session and **retries the call once**. If that fails too, the model is told the
endpoint is down and to report it rather than guess. Error messages carry the
HTTP status (`… (HTTP 502)`).

::: tip Re-authenticating will not help here
**Re-authenticate** swaps a credential. When the endpoint returns 404s or 502s
the credential is fine.
:::

### Playwright — authenticated scraping

`playwright` runs Microsoft's
[`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) (pinned to
`0.0.83`) so the model can navigate, read the **rendered** page and take
screenshots — what `fetch` (raw HTTP, no session) can't do for JS-rendered or
login-gated pages.

- **Browser:** Microsoft Edge by default, so it can use the corporate Edge SSO.
  Change it in **Settings → MCP servers → Playwright browser** or
  `PRECURSOR_PLAYWRIGHT_BROWSER` (`chromium` on machines without Edge).
- **Profile:** `@playwright/mcp`'s shared profile by default, so existing
  sign-ins carry over. The browser opens headed: sign in once, later turns reuse
  the session. `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` pins an isolated profile.

::: warning Trusted, local use
The persistent profile stores a live authenticated session on disk, and headed
sign-in needs a display. Treat it like the host-mode
[command runner](/features/command-runner): single-user, trusted machine only.
:::

### draw.io — diagrams as files, not pictures

`drawio` writes native **`.drawio`** files (mxGraph XML) into a
[workspace](/features/workspaces), inside its sandbox. They diff in git, open in
draw.io, and are editable in Precursor
([Editing diagrams](/features/workspaces#editing-diagrams)).

| Tool | What it does |
| --- | --- |
| `list_workspaces` | Find the `workspace_id` to write into. |
| `search_shapes` | Find real product icons (~700 verified Azure shapes) and their style strings. |
| `list_shapes` | Generic shape / colour / edge presets. |
| `create_diagram` | Build a laid-out diagram from nodes, edges and (nestable) groups. |
| `write_diagram_xml` | Escape hatch: write raw mxGraph XML (validated). |
| `read_diagram` | Read a diagram back to edit it. |

Describe the graph and the server computes the layout. `groups` nest via their
own `group` field (region → VNet → subnet → resource):

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

- `shape` accepts free text (`"azure firewall"`); the result reports what it
  matched.
- `color` tints plain shapes only, never catalogue icons.
- Any preset field also takes a raw mxGraph style (AWS, UML, BPMN…).
- Output is deterministic; existing files are kept unless `overwrite=true`.

### Signing in to WorkIQ and Agent 365

`workiq` has a **preview** toggle: off runs the local stdio launcher (read-only
`ask`); on uses the hosted, OAuth-protected endpoint with full read **and
write**. Sign-in is a browser flow, surfaced by an inline banner when needed.

- A turn never waits on a stale credential it doesn't use. Sign-in is asked for
  only when the model calls a tool on that server; if it doesn't happen, the
  call fails explicitly.
- Tokens are refreshed silently before they expire (a quarter of the token's
  lifetime ahead, at least five minutes). When several credentials lapse at
  once you get **one prompt**.
- Credentials of servers you haven't used for 6 hours stop being kept warm.
- Servers sharing a credential share the sign-in button: the others read
  `signs in with WorkIQ Teams`.

Tune with the `PRECURSOR_WORKIQ_*` settings
([configuration](/reference/configuration#mcp-tool-servers)). How the refresh
works is in the [MCP reference](/reference/mcp#workiq-and-agent-365-credentials).

#### When a sign-in prompt needs explaining

Collect the browser and backend traces of every sign-in decision, redacted, on
your clipboard, from the browser console:

```js
await precursorWorkiqAuthReport()
```

The backend side is also at `GET /api/mcp/auth/diagnostics` and logged on the
`precursor.mcp.auth` channel (`PRECURSOR_WORKIQ_AUTH_LOG_LEVEL`, default
`debug`). Details: [Auth diagnostics](/reference/mcp#auth-diagnostics).

<!-- The anchor is pinned: it predates the Planner/Word/Excel servers and is
     linked from the changelog and released docs. -->
### Agent 365: the hosted `workiq-*` servers {#agent-365-workiq-teams-and-workiq-user}

| Server | Covers |
| --- | --- |
| `workiq-teams` | Teams: chat and channel messages, members, presence, files. |
| `workiq-user` | Directory: profiles, managers, direct reports. |
| `workiq-planner` | Planner: plans, tasks and goals. |
| `workiq-word` | Word: create a document, read it, add and reply to comments. |
| `workiq-excel` | Excel: create a workbook, read it, add and reply to comments. |

- **One sign-in covers all five.** The WorkIQ preview is a separate credential.
- **They need your tenant GUID** (`common` / `organizations` are rejected).
  Resolved from **Settings → MCP servers → Microsoft 365 tenant**, then
  `PRECURSOR_WORKIQ_TENANT_ID`, then auto-discovered from a WorkIQ preview
  sign-in. Until known, the entries say they are unconfigured.

::: tip Why not `workiq-productivity` (or `-mail`, `-files`, `-sharepoint`)?
Those sit behind a second Entra resource (`invalid_audience` with the shared
credential), and the hosted `workiq` preview already covers mail, calendar and
files. Microsoft's `workiq-productivity` *plugin* is a set of prompt skills, not
a server.
:::

### Approving tool calls

Whether a tool runs on its own follows the conversation's
[tool approval policy](/features/approvals). Under **balanced**, only tools the
server marks read-only (`readOnlyHint`) run without asking.

## As a server — exposing your conversations

The built-in **`precursor`** server exposes Precursor's data to MCP hosts. Every
section but **Canvases** is **off by default**; enable them in **Settings → MCP servers →
Precursor capabilities** (**Select all** / **Unselect all** flip them at once).
Only the tools of exposed sections are advertised.

| Section(s) | Tools / what they give |
| --- | --- |
| Topics, messages, chats, agents, live | Read topics (with their slug `path`), messages (with a `permalink` per message), chats, agent tasks, meetings. |
| Search | Substring search across every exposed surface; each hit names its `accessor` (`get_chat`, `get_agent`, …). |
| [IQ](/features/iq) (`iq`, `iq_ask`) | `retrieve` (ranked, cited passages) and `ask` (a cited answer). Each hit is gated by its source's section. |
| Notes and posting | `append_note` files text into a topic; `post_message` runs a full reply. Both return the topic's title, path and URL. |
| [Side chats](/features/chats#side-chats) (`side_chats`) | `create_side_chat` from a topic, a reply or a quote. |
| [Step plans](/features/step-by-step) (`step_plans`) | `create_step_plan`, `list_step_plans`, `get_step_plan`, `amend_step_plan`, `edit_step_plan`, `run_step_plan`, `pause_step_plan`. |
| Skills, memory (`memory_write`), schedules, reminders | Read and manage them. |
| [Agent](/features/agents-mode/artifacts-state#durable-state-the-private-scratchpad) and [workflow](/features/workflows/steps#pipeline-state-what-a-workflow-remembers) state | Durable key/value state. |
| [Workspaces](/features/workspaces) / Edit workspaces | `list_workspaces`, `list_workspace_files`, `read_workspace_file`; edit adds `create_workspace` (empty or git clone), `create_workspace_file`, `write_workspace_file`, `create_workspace_folder`. Agents still ask before each write. |
| [Canvases](/features/canvas) (`canvas`, on by default) | `canvas_guide`, `list_canvases`, `read_canvas`, `write_canvas`, `edit_canvas`: visuals shown inline in a reply, kept in the built-in Canvas workspace. |
| Basic tools | Stateless helpers ([below](#basic-tools)). |

With every section off, only `precursor_info` is advertised. The tool-by-tool
behaviour of step plans and the event relay are in the
[MCP reference](/reference/mcp#the-precursor-server).

### Basic tools

| Tool | What it does |
| --- | --- |
| `get_datetime` | Current date, time, weekday, ISO week and UTC offset, in any IANA `timezone`. |
| `convert_timezone` | Convert an ISO 8601 date-time between zones. |
| `date_math` | Shift a date, or measure a gap (including business days). |
| `calculate` | Arithmetic and common math functions, no arbitrary code. |
| `convert_units` | Length, mass, volume, time, data size, speed, temperature. |
| `generate_uuid` | UUID v4 or v7, up to 100 at a time. |
| `random_value` | Secure URL-safe or hex tokens, integers in a range. |
| `hash_or_encode` | MD5/SHA hashes; base64, URL, hex and HTML encode/decode. |
| `count_text` | Characters, words, lines, sentences, paragraphs, estimated tokens. |
| `get_system_info` | App version, OS, architecture, Python, locale, time zone (no host/user names or paths). |

### Transports

- **stdio** — `python -m precursor.backend.services.mcp.precursor_server`, for a
  host that launches it as a subprocess. Writes made this way reach open windows
  only when Precursor itself launched the process; an external host should use
  HTTP.
- **HTTP** — `/mcp` (streamable HTTP), in-process. **Off by default**,
  loopback-only, Host-header allowlist, no auth.

::: warning Keep MCP-over-HTTP local
The HTTP transport has no authentication and only binds to loopback. Leave it
that way unless you front it with your own authenticating proxy.
:::

See the [architecture reference](/reference/architecture#mcp) for the full
picture.
