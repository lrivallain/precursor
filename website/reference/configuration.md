---
title: Configuration reference
---

# Configuration reference

Every process-level variable (`.env`), plus the database-stored settings that
other pages refer to by key. For the common settings, see the
[Configuration guide](/guide/configuration).

**Where a value lives.** Anything editable in **Settings** is stored in the
database and has no environment variable (enforced by a test). `.env` holds only
what must be known before the database is opened. Two exceptions,
`PRECURSOR_PLAYWRIGHT_BROWSER` and `PRECURSOR_WORKIQ_TENANT_ID`, describe the
machine or tenant: `.env` seeds them and a value saved in **Settings → MCP
servers** wins.

All variables are optional. In a source checkout, copy `.env.example` to `.env`;
an installed instance reads the `.env` in its
[data directory](/features/background-app#where-the-data-lives).

## Server

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_HOST` | `127.0.0.1` | Bind address. Keep it on loopback unless an authenticating proxy fronts the app. |
| `PRECURSOR_PORT` | `8000` | The URL you open. A busy port bumps to the next free one; `precursor service install` [writes the chosen port back](/features/background-app#picking-a-port). In `--dev`, Vite serves this port and the backend uses `PORT + 1`. |
| `PRECURSOR_LOG_LEVEL` | `info` | App and uvicorn log level. |
| `PRECURSOR_LOG_FILE_MAX_BYTES` | `5242880` | Rotation size of [`logs/precursor.log`](/features/background-app#reading-the-log). |
| `PRECURSOR_LOG_FILE_BACKUPS` | `3` | Rotated generations kept. |
| `PRECURSOR_SHUTDOWN_GRACE_SECONDS` | `3` | Wait for in-flight requests (e.g. SSE streams) on Ctrl-C before force-closing. |
| `PRECURSOR_CORS_ORIGINS` | *(empty)* | Comma-separated extra origins. Empty = same-origin only. |

CLI flags: `--port`, `--api-port`, `--host`, `--strict-port` (fail instead of
bumping), `--port 0` (any free port), `--open`, `--dev`, `--no-frontend`.

## Database & data directory

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_DATABASE_URL` | `sqlite+aiosqlite:///./precursor.db` *(checkout)* / `<data dir>/precursor.db` *(installed)* | Async SQLAlchemy URL. PostgreSQL (`postgresql+asyncpg://…`) needs the `postgres` extra. |
| `PRECURSOR_DATA_DIR` | `.precursor` *(checkout)* / user data dir *(installed)* | Holds `workspaces/`, attachment `blobs/`, `drawio/`, `agents/copilot-home/`, `runtime.json` and `logs/`. Relative paths resolve against the working directory. |
| `PRECURSOR_SKILLS_DIR` | *(empty)* | [Skills](/features/skills-memory) folder. Unset: `COPILOT_HOME` → `$XDG_CONFIG_HOME/copilot` → `~/.copilot`. |
| `PRECURSOR_DEFINITIONS_DIR` | `<data dir>/workspaces/definitions` | **WIP.** Folder of [definition files](/features/definitions) (`*.agent.yaml`, `*.workflow.yaml`, `*.summary.yaml`). Replaces the built-in Definitions workspace. |
| `PRECURSOR_DEFINITIONS_SOURCE` | `database` | **WIP.** `files` forces files mode. Normally set by the [migration wizard](/features/definitions#getting-there-from-today-s-data) instead. |
| `PRECURSOR_DEFINITIONS_WORKSPACE` | *(empty)* | **WIP.** Keep definitions in a [workspace](/features/workspaces): its slug, optionally with a subfolder (`team-defs/precursor`). Ignored when `PRECURSOR_DEFINITIONS_DIR` is set. |

A source checkout keeps state beside the code (one sandbox per clone/worktree).
An installed wheel uses the per-user data dir:

| Platform | User data dir |
| --- | --- |
| macOS | `~/Library/Application Support/Precursor` |
| Linux | `$XDG_DATA_HOME/precursor`, else `~/.local/share/precursor` |
| Windows | `%APPDATA%\Precursor` |

## Background app & updates

Used by the [background app](/features/background-app) (supervisor, tray, updater).

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_UPDATE_REPO` | `lrivallain/precursor` | Repository whose releases the update check reads. |
| `PRECURSOR_UPDATE_CHANNEL` | `stable` | `stable` or `nightly`. Empty = match the running build (dev → nightly). **Settings → Updates** wins. |
| `PRECURSOR_UPDATE_EXTRAS` | `kanban` | Extras kept across `precursor service update`, unioned with uv's install receipt. Add `tray` if you use the icon; `-name` drops one. |
| `PRECURSOR_UPDATE_CHECK_TTL_SECONDS` | `900` | Cache lifetime of an update-check result. |
| `PRECURSOR_UPDATE_NOTIFY` | `off` | `notify` posts a system notification when a background check finds a build. **Settings → Updates** wins. |

## Anonymous usage data

See [Anonymous usage data](/features/telemetry). Nothing is sent until the user
opts in from the app; these only turn it off or redirect it.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_TELEMETRY` | *(empty)* | `off` sends nothing, whatever the user chose. `DO_NOT_TRACK=1` and `CI=true` do the same. |
| `PRECURSOR_TELEMETRY_CONNECTION_STRING` | the project's (Sweden Central) | Application Insights connection string the data goes to. Empty disables telemetry. |
| `PRECURSOR_TELEMETRY_POLL_SECONDS` | `900` | How often queued error reports are sent and the daily heartbeat is checked. |

### Installer variables

Read by `install.sh` / `install.ps1` (see [Installation](/guide/installation)):

| Variable | Default | Effect |
| --- | --- | --- |
| `PRECURSOR_CHANNEL` | `nightly` | `stable` installs the latest tagged release from PyPI. |
| `PRECURSOR_EXTRAS` | `kanban,tray` | Extras to install; `none` for the lean core. |
| `PRECURSOR_NO_START` | — | `1` installs the command without registering or starting anything. |
| `PRECURSOR_WHEEL` | — | Install this wheel (path or URL) instead of a channel build. |
| `PRECURSOR_REPO` | `lrivallain/precursor` | Repository whose nightly builds to install. |

## Diagram editor

`.drawio` files open in a self-hosted draw.io editor, downloaded on first use
into `<data dir>/drawio/<version>/` (~53 MB download, ~150 MB on disk). See
[Workspaces → Editing diagrams](/features/workspaces#editing-diagrams).

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_DRAWIO_VERSION` | `v31.3.1` | draw.io release to install; changing it replaces the old one on next use. |
| `PRECURSOR_DRAWIO_DOWNLOAD_URL` | GitHub release `draw.war` | Archive URL template (`{version}` substituted). Point it at a mirror when offline. |

## LLM provider

Providers, credentials, models and presets are set in **Settings → Model** and
**Settings → Presets**; their stored keys are in the
[Models & presets reference](/reference/models).

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_GITHUB_CLI_USER` | *(empty)* | `gh` login that supplies the token (`gh auth token --user …`), for [several signed-in accounts](/guide/configuration#several-accounts-signed-in-to-gh). |

Prompt budgeting (**Settings → Model → Prompt budgeting**):

| Setting | Default | Description |
| --- | --- | --- |
| `llm_max_input_tokens` | `600000` | Estimated prompt tokens per request; oldest turns are dropped first ([`/compact`](/features/context-compression) summarises them instead). |
| `llm_max_tool_result_tokens` | `20000` | Cap on one tool result entering the prompt. |
| `llm_tool_result_keep_turns` | `5` | Tool results over 2,000 characters older than this many user turns are sent as a preview. `0` = always in full. See [automatic trimming](/features/context-compression#automatic-trimming). |
| `llm_max_attachment_chars` | `200000` | Extracted text kept per attached document (1,000–2,000,000); longer text is cut with a note. See [Attachments](/features/attachments#how-much-of-a-document-the-model-sees). |

The [OpenAI-compatible endpoint](/features/openai-endpoint) (`openai_proxy_enabled`,
off by default, and its API key) is set in **Settings → Model**; its URL
follows `PRECURSOR_HOST` / `PRECURSOR_PORT`.

## Backup

Enabled, targeted and retained from **Settings → Backup**. Each run writes `db/`
snapshots, a `blobs/` mirror and a `definitions/definitions-<timestamp>.zip`
of the [definitions folder](/features/workspaces#the-definitions-workspace);
retention applies to snapshots and archives.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_BACKUP_INTERVAL_SECONDS` | `86400` | Minimum time between successful backups. |
| `PRECURSOR_BACKUP_POLL_SECONDS` | `3600` | How often the ticker checks whether a backup is due. |

## Scheduler & reminders

Ticker for [scheduled topics, agents and workflows](/features/scheduler) and
reminders. Each schedule's recurrence is stored in the database.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_SCHEDULER_ENABLED` | `true` | Master switch for every background ticker: scheduler, reminders, retention sweeps, IQ indexer. `false` for a read-only or CI instance. |
| `PRECURSOR_SCHEDULER_POLL_SECONDS` | `30` | Scheduler poll interval. |
| `PRECURSOR_SCHEDULER_CONCURRENCY` | `2` | Scheduled runs executing at once. |
| `PRECURSOR_REMINDER_POLL_SECONDS` | `30` | Reminder poll interval. |

The per-run timeout is **Settings → System → Scheduled run timeout** (default `600` s).

## Retention

Sweeps run at startup and daily (see [Storage & retention](/features/storage)).
Windows are database settings:

| Setting | Default | Where | Description |
| --- | --- | --- | --- |
| `tool_result_retention_days` | `0` (forever) | Settings → System | Replace large tool-result content with a placeholder and drop the model's thinking. |
| `live_transcript_retention_days` | `7` | Settings → Live | Delete a [Live session](/features/live-sessions#transcript-retention)'s transcript segments after it ends. Insights, notes, summary kept. `0` = forever. |
| `workflow_step_retention_days` | `30` | Settings → Workflows | Replace a finished run's step inputs with a placeholder; such steps can't be replayed. Outputs kept. `0` = forever. |
| `agent_event_retention_days` | `30` | Settings → Agents | Delete archived agent timeline events. Running agents are never pruned; results, artifacts and messages kept. `0` = forever. |
| `agent_event_max_per_session` | `2000` | Settings → Agents | Keep only the newest N archived events per agent. `0` = unlimited. |

Poll cadences: `PRECURSOR_TOOL_RESULT_RETENTION_POLL_SECONDS`,
`PRECURSOR_LIVE_TRANSCRIPT_RETENTION_POLL_SECONDS`,
`PRECURSOR_AGENT_EVENT_RETENTION_POLL_SECONDS`, all `86400`, all gated by
`PRECURSOR_SCHEDULER_ENABLED`.

## Precursor IQ

The [Precursor IQ](/features/iq) index (⌘K ranking, Ask, `/api/iq/*`, the
`retrieve` / `ask` MCP tools).

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_IQ_ENABLED` | `true` | `false`: ⌘K uses substring search and `/api/iq/*` returns `404`. |
| `PRECURSOR_IQ_INDEX_POLL_SECONDS` | `20` | Indexer interval for queued changes and missing embeddings (min 5 s). |
| `PRECURSOR_IQ_RECONCILE_POLL_SECONDS` | `3600` | Interval of the check for bulk edits that bypassed the queue. |

**Settings → Precursor IQ**:

| Setting | Default | Description |
| --- | --- | --- |
| `iq_embeddings_enabled` | `false` | Embed every passage (uses quota) and add vector similarity to ranking. |
| `iq_embedding_provider` | `""` | Provider id for embeddings; empty = default provider. An unconfigured provider disables semantic matching. |
| `iq_embedding_model` | `text-embedding-3-small` | Model or Azure deployment name, at 256 dimensions. |
| `iq_ask_provider` | `""` | Provider for Ask answers (with `iq_ask_model`); empty = default. |
| `iq_ask_model` | `""` | Model for Ask answers; empty = chat model. |

## Agents

[Agents mode](/features/agents-mode) is switched on in **Settings → Agents**
(no env default).

| Variable | Default | Description |
| --- | --- | --- |
| `COPILOT_CLI_PATH` | *(empty)* | `copilot` binary to use. Otherwise: the SDK's download cache, then `PATH`. The probe never downloads ([details](/features/agents-mode#pointing-at-a-specific-cli)). |
| `PRECURSOR_AGENTS_MAX_CONCURRENT` | `3` | Agents [executing a turn at once](/features/agents-mode/orchestration#budgets-the-concurrency-governor); others queue. `≤ 0` = unbounded. |
| `PRECURSOR_AGENTS_RETRY_BACKOFF_SECONDS` | `60` | Base delay of [auto-retry](/features/agents-mode/orchestration#retry-auto-recovery), doubling per attempt, up to the agent's `max_retries`. |

| Setting | Default | Description |
| --- | --- | --- |
| `agents_file_change_tracking` | `true` | New sessions record changed files so a [rewind](/features/rewind#restoring-files) can restore them. |

Token budget and max retries are per agent (settings drawer or
[blueprint](/features/agents-mode/orchestration#blueprints-reusable-templates)).

## MCP tool servers

Built-in [MCP](/features/mcp) servers are toggled in **Settings → MCP servers**.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_MCP_IDLE_TTL_SECONDS` | `600` | Idle time before an MCP client session is closed (reattached on next call). |
| `PRECURSOR_MCP_WARMUP_ENABLED` | `true` | Connect enabled servers one by one in the background after startup. Never blocks, never starts an interactive sign-in. `false` = connect on first use. |
| `PRECURSOR_MCP_WARMUP_DELAY_SECONDS` | `5` | Delay before warm-up starts. |
| `PRECURSOR_MCP_WARMUP_GAP_SECONDS` | `1` | Pause between servers during warm-up. |
| `PRECURSOR_GITHUB_MCP_TOOLSETS` | `context,repos,issues,pull_requests,users` | Toolsets of the bundled GitHub server. Fewer toolsets = smaller schemas sent every turn. |
| `PRECURSOR_PLAYWRIGHT_BROWSER` | `msedge` | `playwright` browser: `msedge` (rides corporate SSO), `chromium`, `chrome`, `firefox`, `webkit`, or `default` (omit `--browser`, for builds that reject it). Settings wins. |
| `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` | *(empty)* | `--user-data-dir`. Empty = `@playwright/mcp`'s shared profile, so existing SSO sign-ins carry over. |
| `PRECURSOR_WORKIQ_TENANT_ID` | *(empty)* | Tenant **GUID** for the [Agent 365](/features/mcp#agent-365-workiq-teams-and-workiq-user) servers (`common`/`organizations` are rejected). Settings wins; if both are empty, read from an existing WorkIQ sign-in. |

`mcp_expose` sections of the built-in server, all off by default except
`canvas`: `iq` (`retrieve`), `iq_ask` (`ask`), `workspaces` (read),
`workspaces_write` (create and write; opt-in even for Precursor's agents),
`canvas` ([canvases](/features/canvas): on by default, writes only to the
built-in Canvas workspace), `tools` (`get_datetime`, `calculate`, `date_math`, …).

### WorkIQ / Agent 365 sign-in

Defaults suit an unattended instance (e.g. a
[scheduled workflow](/features/workflows/running#triggers-and-scheduling)).

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_WORKIQ_KEEPALIVE_ENABLED` | `true` | Refresh tokens in the background before they expire. |
| `PRECURSOR_WORKIQ_KEEPALIVE_POLL_SECONDS` | `60` | Keep-alive check interval. Renewal starts a quarter of the token's lifetime (≥ 5 min) before expiry; not configurable. |
| `PRECURSOR_WORKIQ_KEEPALIVE_IDLE_AFTER_SECONDS` | `21600` | Stop refreshing a credential no server has used for this long. `0` = refresh forever. |
| `PRECURSOR_WORKIQ_KEEPALIVE_SURFACE_IDLE_LAPSE` | `true` | When an idle credential has really expired and can't refresh, raise the sign-in banner instead of waiting for your next request. |
| `PRECURSOR_WORKIQ_SILENT_REAUTH_ENABLED` | `true` | Try no-browser re-authentication before prompting. |
| `PRECURSOR_WORKIQ_AUTO_REAUTH_ENABLED` | `true` | Start re-authentication automatically when a credential lapses. |
| `PRECURSOR_WORKIQ_CHAIN_REAUTH_ENABLED` | `true` | After one sign-in, silently renew the other Entra credential too ([one prompt instead of two](/features/mcp#signing-in-to-workiq-and-agent-365)). |
| `PRECURSOR_WORKIQ_LOOPBACK_PORT_FALLBACK` | `true` | If the OAuth callback port (`12798`–`12800`) is busy, use an ephemeral one. `false` = fail instead. |
| `PRECURSOR_WORKIQ_AUTH_LOG_LEVEL` | `debug` | Level of the `precursor.mcp.auth` [trace channel](/features/mcp#when-a-sign-in-prompt-needs-explaining), independent of `PRECURSOR_LOG_LEVEL`. `GET /api/mcp/auth/diagnostics` keeps the full trace regardless. |
