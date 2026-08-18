---
title: Configuration reference
---

# Configuration reference

Most configuration happens **at runtime in the app** (see the
[Configuration guide](/guide/configuration)). This page documents the
**process-level** settings that live in `.env` — every one has a built-in default,
so the file is optional.

The split is deliberate and enforced by a test: anything you can change in
**Settings** is stored in the database and has *no* environment twin, so there is
exactly one place to set it. `.env` keeps only what has to be known before the
database exists — bind address, database URL, data directory, ticker cadences.

Copy `.env.example` to `.env` and uncomment only what you want to override.

## Server

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_HOST` | `127.0.0.1` | Bind address. Keep it on loopback unless you front the app with your own auth. |
| `PRECURSOR_PORT` | `8000` | The URL you open. In `--dev`, the Vite UI runs here and the backend moves to `PORT + 1`. A busy port auto-bumps to the next free one. |
| `PRECURSOR_LOG_LEVEL` | `info` | uvicorn/app log level. |
| `PRECURSOR_SHUTDOWN_GRACE_SECONDS` | `3` | Seconds to wait for in-flight requests (e.g. SSE streams) before force-closing on Ctrl-C, so the port is released promptly. |
| `PRECURSOR_CORS_ORIGINS` | *(empty)* | Comma-separated list of extra allowed origins. Empty means same-origin only, which is what a local-first single-user app wants. |

CLI flags mirror several of these: `--port`, `--api-port`, `--host`,
`--strict-port` (fail instead of bumping a busy port), `--port 0` (any free port),
`--open` (open the browser when ready), `--dev`, and `--no-frontend`.

## Database & data directory

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_DATABASE_URL` | `sqlite+aiosqlite:///./precursor.db` | Async SQLAlchemy URL. Point at Postgres for production. |
| `PRECURSOR_DATA_DIR` | `.precursor` | On-disk working directory. Holds [workspace](/features/workspaces) clones (`workspaces/`), content-addressed [attachment](/features/attachments) blobs (`blobs/`), and the agents runtime's Copilot home (`agents/copilot-home/`). Relative paths resolve against the process working directory. |

```bash
# SQLite (default — no setup)
PRECURSOR_DATABASE_URL=sqlite+aiosqlite:///./precursor.db

# PostgreSQL (needs the `postgres` extra for asyncpg)
PRECURSOR_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/precursor
```

## LLM provider

The provider and its credentials are configured **at runtime** — **Settings →
Model** — not via the environment. The GitHub providers fall back to your
`gh auth login` session when no token is saved. See
[Configuration → Connecting a model](/guide/configuration#connecting-a-model).

## Backup

A periodic copy of the SQLite DB + attachment blobs into a plain folder (e.g. a
OneDrive/Dropbox/iCloud-synced directory). Enable it, pick the target folder, and
set snapshot retention **at runtime** — **Settings → Backup**. Only the scheduling
knobs are env-level:

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_BACKUP_INTERVAL_SECONDS` | `86400` | Minimum time between successful backups. |
| `PRECURSOR_BACKUP_POLL_SECONDS` | `3600` | How often the ticker checks whether a backup is due. |

## Scheduler & reminders

The in-process ticker behind [scheduled topics, agents and workflows](/features/scheduler)
and one-shot reminders. The *cadence* is process-level; each schedule's own
recurrence is stored in the database.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_SCHEDULER_ENABLED` | `true` | Master switch for every background ticker — the scheduler, reminders, and the two retention sweeps. Set `false` for a read-only or CI instance. |
| `PRECURSOR_SCHEDULER_POLL_SECONDS` | `30` | How often the scheduler looks for due runs. |
| `PRECURSOR_SCHEDULER_CONCURRENCY` | `2` | How many scheduled runs may execute at once. |
| `PRECURSOR_REMINDER_POLL_SECONDS` | `30` | How often the reminder ticker looks for due reminders. |

## Retention

Two independent sweeps bound long-term database growth. Each retention window is
set **at runtime** in an `AppSetting`; the sweep runs on startup and repeats daily
via a background ticker. Only the poll cadence is env-level.

| Setting | Default | Where | Description |
| --- | --- | --- | --- |
| `tool_result_retention_days` | `0` (keep forever) | Settings → System | Days before a large tool result's content is replaced in place with a short placeholder. |
| `live_transcript_retention_days` | `7` | Settings → Live | Days after a [Live session](/features/live-sessions#transcript-retention) ends before its transcript segments are deleted. `0` keeps them forever. Insights, notes and summary are preserved. |

Poll cadences (`PRECURSOR_TOOL_RESULT_RETENTION_POLL_SECONDS`,
`PRECURSOR_LIVE_TRANSCRIPT_RETENTION_POLL_SECONDS`) default to `86400` (daily).
Both sweeps are gated by `PRECURSOR_SCHEDULER_ENABLED`.

## Skills directory

The [skills](/features/skills-memory) folder is resolved the way the Copilot CLI
resolves its home: `COPILOT_HOME` → `XDG_CONFIG_HOME/copilot` → `~/.copilot`, with
a `PRECURSOR_SKILLS_DIR` override.

## Agents

[Agents mode](/features/agents-mode) is toggled at runtime (**Settings → Agents**),
which is the only on/off control — there is no env-level default, because
installing the `agents` extra is itself the opt-in. The
[agent orchestrator](/features/agents-mode#orchestrating-agents) governance has two
process-level knobs:

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_AGENTS_MAX_CONCURRENT` | `3` | Concurrency governor — the max agents the [orchestrator](/features/agents-mode#budgets-the-concurrency-governor) lets execute a turn at once. Extra ready agents queue and are released as slots free up. `0` or negative disables the cap (unbounded). |
| `PRECURSOR_AGENTS_RETRY_BACKOFF_SECONDS` | `60` | Base backoff for [auto-retry](/features/agents-mode#retry-auto-recovery) of a failed agent. Delay grows exponentially per attempt (`base × 2ⁿ`); the scheduler re-runs the agent once its retry time is due, up to the agent's `max_retries`. |

Per-agent **token budget** and **max retries** aren't env vars — they're set in
each agent's settings drawer (or baked into a
[blueprint](/features/agents-mode#blueprints-reusable-templates)).

## MCP tool servers

Most [MCP](/features/mcp) built-ins are toggled **at runtime** (**Settings →
MCP**). A few env knobs affect the `playwright` and Agent 365 servers:

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_MCP_IDLE_TTL_SECONDS` | `600` | How long an idle MCP client session is kept warm before it's torn down. A later call re-attaches; lower it to hold fewer child processes open. |
| `PRECURSOR_GITHUB_MCP_TOOLSETS` | `context,repos,issues,pull_requests,users` | Which toolsets the bundled GitHub MCP server registers. Trimming it is the cheapest way to cut that server's schema cost, since tool schemas are re-sent every turn. |
| `PRECURSOR_PLAYWRIGHT_BROWSER` | `msedge` | Browser channel the `playwright` server drives (`--browser`): one of `msedge`, `chromium`, `chrome`, `firefox`, `webkit`, or `default`. Defaults to **Microsoft Edge** so it can ride the corporate SSO/WAM broker for authenticated Entra scraping. Set `chromium` on machines without Edge. `default` **omits `--browser` entirely** — the escape hatch for an `@playwright/mcp` build that predates the flag (fails with `unknown option '--browser'`). Overridable at runtime in **Settings → MCP → Playwright browser** (the DB value wins). |
| `PRECURSOR_WORKIQ_TENANT_ID` | *(empty)* | Microsoft tenant **GUID** the [Agent 365](/features/mcp#agent-365-workiq-teams-and-workiq-user) servers (`workiq-teams`, `workiq-user`) address. Overridden by **Settings → MCP → Microsoft 365 tenant**; when both are empty Precursor reads the tenant off an existing WorkIQ sign-in. Entra rejects `common` / `organizations` here — it must be a GUID. |
| `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` | *(empty)* | Browser profile the `playwright` server uses (`--user-data-dir`). Empty means **reuse `@playwright/mcp`'s own shared, machine-wide profile** — so any Entra/SSO sign-in already onboarded there (incl. via other Playwright-MCP tools) carries over. Set a path to pin an isolated profile for Precursor instead. |

### WorkIQ / Agent 365 sign-in

These govern how the Entra credentials behind the WorkIQ and Agent 365 servers
are kept alive. Defaults are tuned for an unattended install — a
[scheduled workflow](/features/workflows/running#triggers-and-scheduling) has
nobody at a browser.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_WORKIQ_KEEPALIVE_ENABLED` | `true` | Run the background ticker that silently refreshes WorkIQ tokens before they expire. Off means every lapse needs an interactive sign-in. |
| `PRECURSOR_WORKIQ_KEEPALIVE_POLL_SECONDS` | `60` | How often that ticker checks whether a token is near expiry. |
| `PRECURSOR_WORKIQ_KEEPALIVE_REFRESH_MARGIN_SECONDS` | `300` | Refresh once the access token is within this many seconds of expiring. |
| `PRECURSOR_WORKIQ_SILENT_REAUTH_ENABLED` | `true` | Try the hands-free (no-browser) re-authentication path before falling back to an interactive prompt. |
| `PRECURSOR_WORKIQ_AUTO_REAUTH_ENABLED` | `true` | Automatically start a re-authentication when a credential is found to have lapsed, rather than waiting for you to click the banner. |
| `PRECURSOR_WORKIQ_CHAIN_REAUTH_ENABLED` | `true` | After a WorkIQ / Agent 365 sign-in succeeds, immediately retry the **other** Entra credential on the hands-free silent path while the SSO session is hot, so you get [one prompt instead of one per credential](/features/mcp#one-prompt-not-one-per-credential). Set `false` to renew each credential only when it's independently needed. |
| `PRECURSOR_WORKIQ_LOOPBACK_PORT_FALLBACK` | `true` | When an OAuth sign-in's preferred loopback callback port (`12798` / `12799` / `12800`) is already in use, bind a free **ephemeral** port instead of failing. Entra ignores the port of a loopback redirect for public clients, so this is transparent and lets several windows sign in at once. Set `false` for the strict "port is busy — finish that other sign-in first" behaviour. |
| `PRECURSOR_WORKIQ_KEEPALIVE_IDLE_AFTER_SECONDS` | `21600` (6 h) | Stop keeping a WorkIQ / Agent 365 credential warm once no server using it has been called for this long, so a server you never use doesn't [nag you to sign in](/features/mcp#quiet-when-you-re-not-using-it). Usage is tracked per credential and the clock is seeded at startup, so a restart doesn't leave everything cold. `0` disables the back-off and refreshes every signed-in credential indefinitely. |
| `PRECURSOR_WORKIQ_KEEPALIVE_SURFACE_IDLE_LAPSE` | `true` | Let the keep-alive [surface an idle credential's lapse proactively](/features/mcp#surfaced-the-moment-it-lapses-not-on-your-next-request): once an idle token has genuinely expired it probes once, and if the refresh token is dead it raises the sign-in banner instead of waiting for your next request to stall on the doomed handshake. A still-refreshable idle session recovers silently. Set `false` to keep idle credentials completely quiet until you touch them yourself. |

::: tip Where a value comes from
Almost everything you can change in **Settings** is stored in the database and
has **no** environment variable — the panel is the only place to set it.

The two exceptions are `PRECURSOR_PLAYWRIGHT_BROWSER` and
`PRECURSOR_WORKIQ_TENANT_ID`: both describe the *machine or tenant* rather than
a preference, so they're seedable from `.env` and overridden by the
corresponding **Settings → MCP** field when one is saved.

Never read `os.environ` directly in app code — resolve settings through
`services/app_settings.py`.
:::
