---
title: Architecture
---

# Architecture

Precursor is a single-process Python service that serves a JSON API and the built
React SPA from the same uvicorn worker. There is **no Node.js runtime in
production**. A small in-process scheduler and an in-process event bus run
alongside the request handlers.

```mermaid
flowchart LR
    subgraph Browser
      SPA[React SPA]
    end

    subgraph Process[uvicorn worker]
      FAPI[FastAPI app]
      DB[(SQLite / Postgres)]
      LLM["LLM provider<br/>(Copilot / GH Models / mock)"]
      SCHED["Scheduler<br/>(recurring topics + agents)"]
      BUS["Event bus<br/>(SSE pub/sub)"]
      MCPS["MCP server 'precursor'<br/>(stdio + HTTP /mcp)"]
      MCPC["MCP client manager<br/>(built-in + user tool servers)"]
      PLG[Plugin registry]
    end

    GH[GitHub REST API]
    WS["Workspaces<br/>(git clones / local dirs)"]
    JAIL["Docker jail<br/>(cmd-runner)"]
    EXT_MCP[External MCP servers]
    HOST["MCP hosts<br/>(VS Code, CLI agents)"]

    SPA -- "/api/*" --> FAPI
    SPA <-- "SSE /api/events" --- BUS
    FAPI --> DB
    FAPI --> LLM
    FAPI --> GH
    FAPI --> SCHED --> LLM
    FAPI --> MCPC --> EXT_MCP
    MCPC --> JAIL
    MCPC --> WS
    HOST --> MCPS --> DB
    PLG --> FAPI
```

## Process model

A single `uvicorn` worker hosts everything (`precursor/backend/main.py`):

- **FastAPI app** — the JSON API under `/api/*`, the built SPA at `/`, and the MCP
  server's streamable-HTTP endpoint at `/mcp` (gated, loopback-only).
- **Scheduler** (`services/scheduler.py`) — an async ticker + bounded worker pool
  that runs due scheduled topics and agents; started/stopped in the app lifespan.
- **Event bus** (`services/events.py`) — in-process pub/sub so multiple browser
  windows stay in sync over a single SSE stream (`/api/events`).
- **MCP session manager** — the `precursor` MCP server's HTTP transport, also
  started in the lifespan.

Version is CalVer, derived from git tags by hatch-vcs at build time and exposed at
`GET /api/version` (and `/api/health`).

## Request flow: streamed chat

1. `POST /api/topics/{topic_id}/messages/stream` with the user prompt.
2. The router persists the user `Message`, snapshots history, and builds a system
   prompt that includes the linked GitHub issue body + most-recent comments +
   labels, plus any attached skills / memory.
3. Enabled [MCP tool servers](/features/mcp) are opened for the turn; their tools
   are advertised to the provider. The router runs a **tool loop**: stream text,
   collect tool calls, execute them, append `tool` results, call again — up to a
   configured max-rounds — until the model stops requesting tools.
4. Each round is trimmed to a token budget so a few large tool results can't
   overflow the context window.
5. Text deltas and tool-call events stream to the browser over SSE.
6. On stream end (or user "stop"), the assistant turn is persisted using a
   **fresh DB session** (the request-scoped one may be closed by the time the
   generator finishes), and `message.changed` / `stream.ended` events publish.

Scheduled topics run the *same* turn logic off the request path via
`services/turn.py`, driven by the scheduler instead of an HTTP request.

## Database

Models live in `precursor/backend/models/`; async SQLAlchemy 2 via `AsyncSession`.
Highlights:

- **`Topic`** — a self-referencing tree (parent/children). A topic is "scheduled"
  when it has an enabled `TopicSchedule`.
- **`Message`** — per-topic, cascade delete; roles `user` / `assistant` /
  `system` / `tool`. Large `tool` results can be age-pruned in place.
- **`TopicSchedule`** / **`AgentSchedule`** — recurrence config + run state
  (interval, weekday mask, time-of-day, timezone, lease/status).
- **`Workspace`** — a git clone or local directory.
- **`Skill`** — an enablement record for a file-backed `SKILL.md` prompt preset.
- **`Memory`** — long-term notes injected into the system prompt.
- **`Attachment`** — file metadata + a `sha256` pointer; **bytes live on disk** as
  content-addressed blobs, deduped, with a startup GC sweep.
- **`MCPServer`** — user-defined external MCP tool servers.
- **`IssueContextCache`** — cached GitHub issue summary/state/labels (TTL refresh).
- **`AppSetting`** — JSON key/value for runtime settings and **secrets that are
  never echoed back** (only `*_present` booleans are returned).

The schema is managed entirely by **Alembic**: `init_db` runs `alembic upgrade
head` on startup, building a fresh database from migrations or migrating an
existing one — dev and prod alike, no `create_all`.

## GitHub integration

`services/github_client.py` wraps just the endpoints the app needs (list/get
issues, list comments, list labels, create/update issue, post comment). Topic
context is rebuilt on every turn so changes to the linked issue propagate
instantly; the result is cached (`IssueContextCache`) with a TTL. Auth resolves
in order: a token saved in settings, then the GitHub CLI session
(`gh auth token`). With neither, the LLM falls back to the mock provider.

## LLM provider abstraction

`services/llm/base.py` defines a small protocol — two streaming methods (plain
text and a tool-capable event stream) plus `list_models()`. Providers are declared
in `services/llm/registry.py`; `get_llm_provider(session)` reads the active
provider + config from the DB per request and constructs it, falling back to the
mock when credentials are missing. Shipped providers: **GitHub Copilot**
(default), **GitHub Models**, **Azure AI Foundry**, **OpenAI-compatible**, and
**Mock**. Adding a provider is one `ProviderSpec` plus an implementation class.

## MCP

Precursor is both an MCP client and an MCP server.

- **As client** (`services/mcp/client.py`) — an `MCPClientManager` holds the
  tool-server registry: built-ins (`github`, `workiq`, `fetch`, `workspace-fs`,
  `cmd-runner`, `precursor`) plus user-defined servers. Servers are toggled in
  Settings; sessions open per chat turn.
- **As server** (`services/mcp/precursor_server.py`) — a `FastMCP` server exposing
  Precursor's own data, gated per-section by `mcp_expose` (off by default), over
  **stdio** and an in-process **HTTP** transport at `/mcp` (off by default,
  loopback-only, Host-header allowlisted).

See the [MCP feature guide](/features/mcp) for the user-facing side.

## Scheduler

`services/scheduler.py` drives recurring topics **and** scheduled agents: a single
async ticker enqueues due `TopicSchedule` and `AgentSchedule` rows, a bounded
worker pool runs each, with DB row leasing for crash recovery. Scheduled prompts
that start with a slash command are dispatched to that command's backend action;
`/guard` directives gate a run behind a cheap MCP probe. See the
[scheduler feature guide](/features/scheduler).

## Workspaces

A `Workspace` is a git clone or local directory the assistant can browse and edit.
`services/workspace_git.py` clones/pulls/commits (token injected at op time, never
stored); `services/workspace_fs.py` does sandboxed file ops — every path is routed
through `safe_join`, which rejects traversal outside the workspace root and blocks
`.git`. The same sandbox backs the `workspace-fs` MCP server.

## SPA

Vite + React 19 + Tailwind, built to `frontend/dist` and bundled inside the wheel.
All HTTP goes through `src/lib/api.ts`; streaming chat uses a manual SSE reader
(`src/lib/sse.ts`) since it POSTs a JSON body; cross-window sync uses the
`/api/events` SSE stream. Theming is via CSS variables (`light` / `dark` /
`system`).

## Security & deployment model

::: warning Single-user, no auth
Precursor is designed as a **single-user, local-first** app and ships with **no
authentication**. Run it bound to `127.0.0.1` (the default) and don't expose it to
a network without your own authenticating reverse proxy.
:::

Specifically:

- The API and SPA have **no auth** — anyone who can reach the port has full access
  to your topics, settings, and stored tokens.
- The optional [command-runner](/features/command-runner) can execute
  shell/python/node — keep the Docker jail enabled.
- The [MCP-over-HTTP](/features/mcp) transport is off by default and only binds to
  loopback.
- Secrets (GitHub token, provider keys) live in the local DB and are **never
  echoed** by the API — only `*_present` booleans are returned.

See [SECURITY.md](https://github.com/lrivallain/precursor/blob/main/SECURITY.md)
for vulnerability reporting.
