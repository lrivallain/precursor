# Architecture

Precursor is a single-process Python service that serves a JSON API and the
built React SPA from the same uvicorn worker. There is no Node.js runtime in
production. A small in-process scheduler and an in-process event bus run
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

- **FastAPI app** — JSON API under `/api/*`, the built SPA at `/`, and the MCP
  server's streamable-HTTP endpoint at `/mcp` (gated, loopback-only).
- **Scheduler** (`services/scheduler.py`) — an async ticker + bounded worker
  pool that runs due "scheduled" topics and scheduled agents; started/stopped in
  the app lifespan.
- **Event bus** (`services/events.py`) — in-process pub/sub so multiple browser
  windows stay in sync over a single SSE stream (`/api/events`). A contextvar
  carries the originating client id so a window suppresses its own echoes.
- **MCP session manager** — the `precursor` MCP server's HTTP transport task
  group, also started in the lifespan.

Version is CalVer, derived from git tags by hatch-vcs at build time and exposed
at `GET /api/version` (and `/api/health`).

## Request flow: streamed chat

1. `POST /api/topics/{topic_id}/messages/stream` with the user prompt.
2. The router persists the user `Message`, snapshots history, and builds a
   system prompt that includes the linked GitHub issue body + most-recent
   comments + labels, plus any attached skills/memory.
3. Enabled MCP tool servers are opened for the turn; their tools are advertised
   to the provider. The router runs a **tool loop**: stream text, collect tool
   calls, execute them, append `tool` results, call again — up to a configured
   max-rounds — until the model stops requesting tools.
4. Each round is trimmed to a token budget (`services/context_budget.py`) so a
   few large tool results can't overflow the context window.
5. Text deltas and tool-call events stream to the browser over SSE.
6. On stream end (or user "stop"), the assistant turn is persisted using a
   **fresh DB session** (the request-scoped one may be closed by the time the
   generator finishes), and `message.changed` / `stream.ended` events publish.

Scheduled topics run the *same* turn logic off the request path via
`services/turn.py`, driven by the scheduler instead of an HTTP request.

## Database

- Models live in `precursor/backend/models/`. Async SQLAlchemy 2 via
  `AsyncSession` (`db.py`).
- `Topic` — self-referencing tree (parent/children); `kind` is
  `standard | schedule_root | scheduled`.
- `Message` — per-topic, cascade delete; roles `user/assistant/system/tool`.
- `TopicSchedule` — recurrence config + run state for a scheduled topic
  (interval, weekday mask, time-of-day, timezone, lease/status).
- `AgentSchedule` — the same recurrence + run state, one-to-one on an
  `AgentSession`, so an agent re-runs its task on a cadence.
- `Workspace` — a git clone or a local directory the assistant can browse/edit.
- `Skill` — enablement record for a file-backed prompt preset (`/name`
  invocation); content lives in a shared `SKILL.md` file (see Skills & memory).
- `Memory` — long-term notes injected into the system prompt.
- `Attachment` — image blobs bound to messages (vision content-parts).
- `MCPServer` — user-defined external MCP tool servers (transport, headers).
- `IssueContextCache` — cached GitHub issue summary/state/labels (TTL refresh).
- `AppSetting` — JSON key/value store for runtime-editable settings (theme,
  model, MCP toggles, `mcp_expose`, jail config, **secrets that are never echoed
  back** — only `*_present` booleans are returned).
- Schema is managed entirely by **Alembic**: `init_db` runs `alembic upgrade
  head` on startup, which both builds a fresh database from migrations and
  migrates an existing one (dev and prod alike — no `create_all`). Generate a
  migration from model changes with `make migration m="…"` (autogenerate). A
  database stamped at a squashed-away revision is auto-adopted to the current
  baseline on next startup (a version-row update only — no schema/data change).

Runtime settings layer over env defaults: `services/app_settings.py` resolves
each setting as "env/`.env` default, overridden by an `AppSetting` row if
present, clamped to a sane range".

## GitHub integration

`services/github_client.py` wraps just the endpoints the app needs (list/get
issues, list comments, list labels, create/update issue, post comment). Topic
context is rebuilt on every turn so changes to the linked issue propagate
instantly; the result is cached (`IssueContextCache`) with a TTL.

Auth resolves in `services/github_auth.py`, in order:

1. A token saved in the app settings (Settings → GitHub).
2. The **GitHub CLI** session (`gh auth token`) if signed in via `gh auth login`.

A token is never required to start: with neither source, the LLM falls back to
the mock provider. The resolved source is surfaced to the UI as
`settings | gh-cli | none` (tokens themselves are never returned).

## LLM provider abstraction

`services/llm/base.py` defines a small protocol — two streaming methods
(plain text and a tool-capable event stream) plus `list_models()`:

```python
class LLMProvider(Protocol):
    name: str
    def stream_chat(self, *, model, messages) -> AsyncIterator[str]: ...
    def stream_chat_with_tools(self, *, model, messages, tools) -> AsyncIterator[ProviderEvent]: ...
    async def list_models(self) -> list[LLMModel]: ...
```

Providers are declared in `services/llm/registry.py` — each `ProviderSpec`
carries a label, its config fields (rendered in Settings; secrets redacted),
and a builder. `get_llm_provider(session)` reads the active provider id + its
config from the DB (Settings → Model) per request and constructs it, falling
back to the mock when credentials are missing. Shipped providers:

- `GitHubCopilotProvider` — **default**; the Copilot model catalogue (Claude,
  Gemini, GPT, …) via a `gho_*` token, OpenAI-compatible at
  `https://api.githubcopilot.com`.
- `GitHubModelsProvider` — GitHub Models inference
  (`https://models.github.ai/inference`), PAT with `models:read`.
- `AzureFoundryProvider` — Azure OpenAI / AI Foundry deployments via
  `AsyncAzureOpenAI` (endpoint + key + deployment).
- `OpenAICompatibleProvider` — OpenAI, Mistral, Hugging Face, Ollama, and any
  OpenAI-compatible gateway (base URL + key).
- `MockProvider` — deterministic streamed reply, used automatically when no
  credentials are available.

Shared OpenAI-compatible plumbing (message/tool translation, the tool-call
delta accumulator) lives in `services/llm/_openai_compat.py`. Adding a provider
is one `ProviderSpec` in the registry plus an implementation class.

## MCP

Precursor is *both* an MCP client and an MCP server, with working transports.

**As client** (`services/mcp/client.py`) — `MCPClientManager` holds a registry
of tool servers. Built-ins ship in-tree as stdio subprocesses or remote
streamable-HTTP: `github`, `workiq`, `fetch`, `workspace-fs`, `cmd-runner`, and
`precursor` itself. Users add their own (`MCPServer` rows). Each server is
toggled in Settings (the `mcp_enabled` map); sessions are opened per chat turn
and their tools surfaced to the provider. A host-dependency *preflight* gates
enabling (e.g. `cmd-runner` needs Docker when its jail is on). `workiq` also has
a **preview** toggle (`mcp_workiq_preview`): off it runs the local stdio launcher
(read-only `ask`); on it switches to the hosted, OAuth-protected HTTP endpoint
(`https://workiq.svc.cloud.microsoft/mcp`) for the full read **and write**
surface. The OAuth browser flow is driven via the SDK's `OAuthClientProvider`
(`services/mcp/workiq_preview.py`), with tokens cached in `AppSetting`. Tokens
are refreshed silently when possible; background connects (catalog probes,
warm-pool workers, chat turns) use a *non-interactive* provider that never pops
a browser — when a full sign-in is required they fail fast with a `needs_auth`
state. The user restarts the browser flow on demand from Settings
(`POST /api/mcp/servers/workiq/reauthenticate`, single-flight guarded). The same
sign-in is surfaced *inline* in the main app (no Settings detour) by the global
`McpAuthBanner`: chat/topic/workspace turns hold and stream an `mcp_auth_required`
event from their pause-and-resume gate, and the Agents runtime emits the same
event (as a synthetic timeline entry) when it has to skip an enabled OAuth server
for lack of credentials. A scheduled `/guard` probe (`services/scheduled_commands.py`)
that finds its server parked in `needs_auth` does the same over the cross-window
bus (`mcp.auth_required`) — instead of failing open into a turn that would just
error — and skips the run with a durable transcript note until the user signs in.
The reauthenticate route also drops idle agent sessions so the next dispatch
rebuilds with the fresh token.

**As server** (`services/mcp/precursor_server.py`) — a `FastMCP` server named
`precursor` exposing Precursor's own data: topics, messages, search, skills,
memory (read + `memory_write` to store/edit entries), `post_message` (runs a full
turn), and schedules. Every tool is gated by
a per-section `mcp_expose` toggle (default **off** — exposing conversation
history outbound is opt-in). Two transports, same tools:

- **stdio** — `python -m precursor.backend.services.mcp.precursor_server`; how a
  host like VS Code launches it as a subprocess.
- **HTTP** — mounted in-process at `/mcp` (streamable-http). Off by default,
  loopback-only, with a Host-header allowlist (DNS-rebinding protection) and no
  auth — so it never answers on a non-loopback bind.

`services/mcp/server.py` is the descriptor behind `GET /api/mcp/server/info`.

## Scheduler

`services/scheduler.py` drives recurring "scheduled" topics **and** scheduled
agents: a single async ticker enqueues due `TopicSchedule` *and* `AgentSchedule`
rows (tagged `(kind, id)`), a bounded worker pool runs each — topics via
`services/scheduled_commands.py` under a timeout, agents by re-triggering the
agent's task — with DB row leasing for crash recovery. Recurrence supports
interval, weekday mask, and daily time-of-day in a timezone
(`services/schedule_timing.py`).

A scheduled prompt that begins with a slash command (e.g. `/agent run the
tests`, `/gh-sync`) is dispatched to the command's backend action by
`services/scheduled_commands.py` — the same commands the chat composer offers on
the `topic` surface, plus user skills — instead of being sent to the LLM.
Anything else runs a normal generation turn via `services/turn.py` (the same
path as manual chat). Keep the dispatcher's `BUILTIN_TOPIC_COMMANDS` in sync
with the topic surface in `frontend/src/lib/commands.ts`.

A recurring `/agent <uuid> <follow-up>` nudges the *same* agent every run, so its
transcript — and the input tokens replayed each turn — grows without bound. Two
directives wipe the agent's context first while keeping the same uuid (so the
schedule keeps resolving), so each run starts from a clean transcript:

- `/agent <uuid> /clear <follow-up>` — reset, then send `<follow-up>`. Maps to
  `AgentManager.clear_session(..., keep_id=True)`, which deletes the SDK's
  on-disk session and reuses the id rather than minting a new one.
- `/agent <uuid> /run [extra]` — reset, then replay the agent's own
  `task_prompt` (+ optional one-off `[extra]`). This keeps the instructions in
  **one** place (the agent) instead of the schedule re-sending them every run;
  the recurring prompt shrinks to a tiny nudge. Maps to
  `AgentManager.rerun_task(...)`.

An agent can also carry its **own** recurrence via `AgentSchedule` (a one-to-one
row on `AgentSession`, edited from the agent's settings drawer). Each due tick
re-runs the agent's stored `task_prompt` directly — `clear_context` selects
`AgentManager.rerun_task(...)` (fresh transcript, same uuid) vs `send_message(...)`
(follow-up in the existing conversation). This is the first-class equivalent of a
scheduled topic that nudges `/agent <uuid> /run`, without a hosting topic. A run
is skipped (not failed) while the agent is mid-turn, archived, or task-less.

A scheduled prompt may also be prefixed with one or more `/guard` directives that
gate the whole run behind a cheap, deterministic MCP probe (no LLM, ~0 tokens):

```
/guard non-empty workiq fetch {"entityUrls": ["/me/mailFolders/<folder-id>/messages?$select=id&$top=1"]}
/agent <uuid> /run
```

`/guard <predicate> <server> <tool> [json-args]` calls a single MCP tool via the
chat-side `MCPClientManager` and classifies its result; `non-empty` runs only
when the probe returns rows, `empty` runs only when it returns none. When the
predicate isn't satisfied the run is skipped silently — no LLM turn, no chat
message — and just reschedules. This stops a poller (e.g. an inbox watcher) from
burning a full ~70K-token turn every tick only to find nothing to do.

Emptiness is read across common result shapes, including the WorkIQ/`fetch`
envelope `{"results": [{"data": {"value": [...]}, "statusCode": 200}]}` (the rows
live at `results[i].data`, not the top level). A malformed or failing guard
*fails open* (the run proceeds) so a typo or a transient MCP error can never
silently disable a schedule. A server that needs interactive sign-in is the one
exception: instead of failing open into a turn that would just error, the guard
surfaces a re-authenticate prompt (the `mcp.auth_required` bus event +
transcript note) and skips until the user signs in. An explicit **Run now**
(`POST /api/schedules/{topic_id}/run`) is a *forced* run: the scheduler flags the
topic so the guard still gates (an empty probe still skips) but records the skip
*visibly* — a manual trigger that finds no work says so instead of appearing to
do nothing, while an automatic tick stays silent to avoid posting every poll. See
`_evaluate_guards` in `services/scheduled_commands.py`.

## Workspaces

A `Workspace` is a git clone or a local directory the assistant can browse and
edit. `services/workspace_git.py` clones/pulls/commits (token injected at op
time, never stored on the row); `services/workspace_fs.py` does sandboxed file
ops — every path is routed through `safe_join`, which rejects traversal outside
the workspace root and blocks `.git`. The same sandbox backs the `workspace-fs`
MCP server so the assistant edits files within the jail.

## Command runner (jail)

`services/cmd_runner.py` + the `cmd-runner` MCP server execute bash/python/node
either inside a throwaway **Docker container** (the default "jail": bind-mounted
workdir, network off, cpu/memory/pid limits) or — when the jail is disabled —
directly on the host with full disk access (a loud, opt-in disclaimer). Enabling
the server preflights Docker availability against the effective jail setting.

## Skills & memory

- **Skills** (`Skill`, `routers/skills.py`, `services/skills.py`) — reusable
  prompt presets invoked as `/name` in chat; the SPA expands them inline. Content
  is stored as shared `<copilot_home>/skills/<name>/SKILL.md` files (YAML
  frontmatter `name`/`description` + markdown body = instructions), the same
  format the GitHub Copilot CLI uses, so skills are interoperable across tools.
  The skills dir is resolved like the CLI's home (`COPILOT_HOME` →
  `XDG_CONFIG_HOME/copilot` → `~/.copilot`), with a `PRECURSOR_SKILLS_DIR`
  override. The `skills` table is reduced to an enablement record: a discovered
  skill is disabled until opted in, and if its file is renamed or deleted the
  enablement row is dropped. Skills created before this model still live in the
  DB ("legacy"); they keep working and expose a **Migrate** action that writes
  the `SKILL.md` and keeps the row as an enablement record.
- **Memory** (`Memory`, `routers/memories.py`, `services/memories.py`) —
  long-term notes injected into the system prompt of topic chats, flat chats, and
  agent sessions so context persists across conversations. Editable from Settings,
  from chat via `/memory-store`, `/memory-list`, and `/memory-update`
  (store/update on the topic/chat/agent surfaces; `/memory-list` on topic/chat),
  or by the model itself through the `list_memories`/`store_memory`/`update_memory`
  MCP tools.

## SPA

- Vite + React 19 + Tailwind. Built to `frontend/dist`; in production FastAPI
  serves it from there, and the build is also **bundled inside the wheel**
  (`precursor/frontend_dist`) so an installed package is self-contained.
- All HTTP goes through `src/lib/api.ts`. Streaming chat uses a manual SSE
  reader (`src/lib/sse.ts`) because it POSTs a JSON body (not `EventSource`);
  cross-window sync uses the `/api/events` SSE stream.
- Theming via CSS variables (`light` / `dark` / `system`), toggled by adding
  `.dark` to `<html>`.
- The SPA fetches `/api/plugins` on boot — extensions describe themselves
  declaratively (kind + slot + config) and are rendered by renderers registered
  through `src/lib/plugins.ts`.

## Build, versioning & packaging

- **uv** is the toolchain (env, run, build, release); `pyproject.toml` is a uv
  project with a committed `uv.lock`.
- Version is **CalVer** (`YYYY.M.MICRO`) derived from git tags by **hatch-vcs**
  at build time — no literal to edit (`precursor.__version__`).
- A conditional build hook (`hatch_build.py`) bundles the built SPA into the
  wheel only for real (non-editable) builds, so `uv sync` / dev / CI never need
  a frontend build.
- CI (`.github/workflows/ci.yml`) runs ruff, mypy (strict), pytest, and the
  frontend typecheck+build on every PR. A tag push (`v*`) triggers
  `release.yml`, which builds the wheel and publishes a GitHub Release. See
  [../RELEASING.md](../RELEASING.md).

## Plugin contract

See [plugins.md](plugins.md).
