---
title: API reference
---

# API reference

::: info Coming soon
A full, generated API reference is on the roadmap. This page will document the
`/api/*` surface — endpoints, request/response schemas, and the SSE event stream —
generated from FastAPI's OpenAPI schema so it stays in lockstep with the code.
:::

Until then, the fastest way to explore the API is the **interactive OpenAPI docs**
that FastAPI serves for a running instance.

## Interactive docs (live instance)

With Precursor running locally, open:

- **Swagger UI** — [`/api/docs`](http://127.0.0.1:8000/api/docs)
- **ReDoc** — [`/api/redoc`](http://127.0.0.1:8000/api/redoc)
- **OpenAPI schema (JSON)** — [`/api/openapi.json`](http://127.0.0.1:8000/api/openapi.json)

(Adjust the port to match your `--port`.)

::: tip
FastAPI's interactive docs live under `/api/*` so the root `/docs` path can
serve this documentation site **in-app** — see
[Serving the docs in-app](#serving-the-docs-in-app) below.
:::

## Serving the docs in-app

This documentation site (the VitePress project in `website/`) is also served by
the app itself at **[`/docs/`](http://127.0.0.1:8000/docs/)**, so you can read it
without leaving Precursor (there's a **Documentation** entry in the command
palette and the About dialog).

- **Production / one-port `precursor`** — the site is pre-built with base
  `/docs/` (`make docs`) and bundled into the wheel; the backend serves it
  statically at `/docs/*`, resolving VitePress clean URLs.
- **`precursor --dev`** — a live VitePress dev server runs on a hidden port and
  the SPA's Vite proxies `/docs` to it, so editing any `website/**` markdown
  **hot-reloads** in the browser.
- **GitHub Pages** is unaffected: it builds the same source with the default
  base `/` in its own workflow.

## Surface at a glance

The JSON API lives under `/api/*`. Routers are grouped by domain:

| Area | What it covers |
| --- | --- |
| `topics` | CRUD for topics, the topic tree, read/unread state, and per-topic messages. Reads carry the topic's immutable `public_id`; `GET /by-slug/{slug}` and `GET /by-public-id/{public_id}` back the `/topics/…` and `/t/{uuid}` routes. `GET /api/topics` and `GET /api/topics/archived` accept `?collection_id=`, and `GET /api/topics/tree` already did. |
| `collections` | CRUD for [collections](/features/collections); deleting one re-homes its topics via `?reassign_to=`. |
| `chat` | Streamed chat (`.../messages/stream`) over Server-Sent Events. |
| `chats` | Quick throwaway chats, including read/unread state. `POST /{id}/promote` turns one into a topic and takes `?collection_id=` for where it lands. |
| `commands` | The `/slash` [commands](/features/skills-memory) a topic composer can run. |
| `attachments` | Upload / fetch [attachments](/features/attachments) on a topic or chat. |
| `settings` | Runtime settings and provider/GitHub configuration (secrets never echoed). |
| `llm` | The provider catalogue and the models the active provider offers. |
| `me` | The connected GitHub identity for the sidebar persona, plus `GET /api/me/copilot` for Copilot AI-credit usage (both degrade to `null` when no token is configured). |
| `github` | Issue/label/comment operations behind topic linking. |
| `github/projects` | GitHub Projects v2 columns and cards behind the [Kanban board](/features/kanban). |
| `mcp` | Tool-server registry, enable/disable, and OAuth (re)authentication. `GET /api/mcp/auth/diagnostics` returns the [WorkIQ sign-in trace](/features/mcp#when-a-sign-in-prompt-needs-explaining) — settings in force, a per-credential fact sheet (token/refresh-token presence, expiry, idle time, state) and the recent auth-episode records. Token values never leave the process. |
| `skills` / `memories` | Skill enablement and long-term memory. |
| `roles` | Assistant [roles](/features/skills-memory) (persona presets). |
| `…/schedule` | Recurrence and **Run now**, as a sub-resource of the thing being scheduled (`/api/topics/{id}/schedule`, `/api/agents/{id}/schedule`) rather than a top-level router. |
| `reminders` | One-shot [reminders](/features/scheduler) that resurface a topic or chat. |
| `agents` | Agent sessions, timelines, and read/unread state, plus [orchestration](/features/agents-mode/orchestration): `GET /metrics` (fleet rollup) and `GET /inbox` (everything waiting on you), `blueprints` CRUD + `/instantiate`, per-agent `/start` (launch a parked agent), `/runs` and `/runs/{runId}` (the agent's [execution history](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run) — trigger, status, capability snapshot, per-run token spend), `/events` (the transcript; every event carries the `agentRunId` it belongs to, and `?agentRunId=` narrows the reply to one run — a run belonging to another agent is a `404`), `/artifacts` (list/create plus `GET /artifacts/{id}` and `GET /artifacts/{id}/raw` for a single artifact and its kind-typed raw body — a `link` artifact redirects to its URL; the list accepts `?runId=` to scope to one run), `/state` (the [durable cross-run scratchpad](/features/agents-mode/artifacts-state#durable-state-the-private-scratchpad): `GET` to list, `PUT` to upsert by key, `DELETE /state/{key}` or `DELETE /state` to reset), and `/triggers`, and the public `POST /hooks/{token}` webhook. |
| `workflows` | [Workflow](/features/workflows) definitions, steps, lifecycle (`/run`, `/pause`, `/resume`, `/cancel`, `/retry`), run traces, per-step replay, approval and tool-permission decisions, pipeline state, schedules, and the public `POST /hooks/{token}` webhook. See the [workflows API reference](/features/workflows/reference#api-surface). |
| `workspaces` | [Workspace](/features/workspaces) clones, the sandboxed file tree, file reads/writes, and workspace chat. |
| `live` | [Live sessions](/features/live-sessions) — transcript segments, insights, notes, summary, and attendees. |
| `stt` | Short-lived Azure Speech token minting for live sessions. |
| `search` | The cross-entity lookup behind the ⌘K command palette. |
| `refine` | One-shot text rephrasing for the notes panel and composer. |
| `stats` | Token-usage rollups and the system footprint for **Settings → Usage stats**, plus the [storage cleanup](/features/storage) cockpit: `GET /api/stats/cleanup` previews what each retention sweep would free (a dry run — nothing is deleted), `POST /api/stats/cleanup/{key}` runs one on demand, and `POST /api/stats/compact` `VACUUM`s the database so freed pages return to the filesystem. |
| `transfer` | [YAML export/import](/features/transfer) of agents and workflows: `GET /workflows/{id}` and `GET /agents/{id}` download a definition; `POST /preview` reports name conflicts without writing; `POST /import` applies them with a per-agent `replace` / `create` / `link` resolution. |
| `plugins` | Descriptors for frontend extensions contributed by plugins. |
| `drawio` | Status and on-demand install of the self-hosted [draw.io editor](/features/workspaces#editing-diagrams) (`GET /api/drawio/status`, `POST /api/drawio/install`). |

Health and version:

- `GET /api/health` — liveness + version.
- `GET /api/version` — the CalVer version (derived from git tags at build time).

::: warning One route lives outside `/api/*`
`GET /raw/{slug}/{path}` serves a [workspace](/features/workspaces) file straight
from its working tree, so HTML renders and relative links inside a file resolve
naturally. It is **read-only and unauthenticated by design**, like the rest of
this single-user app — one more reason to keep Precursor bound to loopback.
:::

## Addressing an agent

An agent's URL-safe identity is its **`public_id`** — the UUID behind
`/agents/{uuid}` deep links, `/agent <uuid>` nudges and
[transfer](/features/transfer) lookups. It is stable for the life of the agent
and independent of any Copilot SDK session handle, which now belongs to an
individual [run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)
rather than the agent. `AgentSessionRead` also embeds a nullable `current_run`,
so one fetch tells you both what the agent *is* and what it's doing right now.

## Real-time events

`GET /api/events` is a Server-Sent Events stream the SPA subscribes to for
cross-window sync. Requests carry an `X-Client-Id` header so the originating
client's own events are filtered back out.

| Event | Fires when |
| --- | --- |
| `topic.changed` | A topic's metadata or state is mutated. |
| `message.changed` | A message is added or edited in a topic or chat. |
| `stream.started` | A chat turn begins. |
| `stream.ended` | A chat turn completes. |
| `read.changed` | A conversation is marked read (distinct from `message.changed`). |
| `reminder.changed` | A [reminder](/features/scheduler) is created, fires, or is cleared. |
| `agent.changed` | An [agent](/features/agents-mode) session's state or event stream moves. |
| `meeting.changed` | A [live session](/features/live-sessions) is created, renamed, ended, or deleted. |
| `workflow.changed` | A [workflow](/features/workflows) step advances, its status flips, or its definition is edited. Carries the run status and workflow name so a client can raise a notification without re-fetching. |
| `mcp.auth_required` | An [MCP server](/features/mcp) needs an interactive sign-in. |
| `mcp.auth_url` | The OAuth authorization URL to open for that sign-in. |
| `mcp.auth_resolved` | An MCP server sign-in completed. |

`agent.changed` and `read.changed` carry an `agent_run_id` alongside
`agent_session_id`, so a listener can tell *which* execution of a shared agent
moved.

Streamed chat responses are their own SSE stream, delivering text deltas and
tool-call events for a single turn. A turn that dies (a provider rejection, the
tool-round cap) emits an `error` event *and* persists an `Error: …` system
message, so the failure is still there after a reload.

A `tool_result` event carries the call id, name, arguments, result text and
error flag — plus `link` (`{slug, path}`) when the tool read or wrote a
[workspace](/features/workspaces) file, which the UI turns into an **Open** chip.
The same object is stored on the tool message's `tool_calls` metadata, so the
chip survives a reload without re-reading the result body.

`POST .../messages/stream` accepts `retry_message_id` to **replay** such a turn:
instead of persisting a new user message it reuses that one — attachments and
all — and deletes every message recorded after it. The id must name a user turn
of the same container (`400` otherwise, `404` when unknown).

::: tip Contributions welcome
Want to help build the generated reference? See the
[contribution guide](/contributing/).
:::
