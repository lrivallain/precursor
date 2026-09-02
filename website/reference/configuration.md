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
exactly one place to set it — bar two deliberate exceptions, noted at the foot of
this page. `.env` keeps only what has to be known before the database exists — bind address, database URL, data
directory, ticker cadences.

Copy `.env.example` to `.env` and uncomment only what you want to override.

## Server

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_HOST` | `127.0.0.1` | Bind address. Keep it on loopback unless you front the app with your own auth. |
| `PRECURSOR_PORT` | `8000` | The URL you open. In `--dev`, the Vite UI runs here and the backend moves to `PORT + 1`. A busy port auto-bumps to the next free one — and `precursor service install` writes the port it settled on back to the [instance `.env`](/features/background-app#picking-a-port) so it stays put. |
| `PRECURSOR_LOG_LEVEL` | `info` | uvicorn/app log level. |
| `PRECURSOR_LOG_FILE_MAX_BYTES` | `5242880` | Size at which `logs/precursor.log` rotates. The app writes [its own log file](/features/background-app#reading-the-log), so this is what caps it — nothing else prunes it. |
| `PRECURSOR_LOG_FILE_BACKUPS` | `3` | How many rotated generations (`precursor.log.1`, …) to keep. |
| `PRECURSOR_SHUTDOWN_GRACE_SECONDS` | `3` | Seconds to wait for in-flight requests (e.g. SSE streams) before force-closing on Ctrl-C, so the port is released promptly. |
| `PRECURSOR_CORS_ORIGINS` | *(empty)* | Comma-separated list of extra allowed origins. Empty means same-origin only, which is what a local-first single-user app wants. |

CLI flags mirror several of these: `--port`, `--api-port`, `--host`,
`--strict-port` (fail instead of bumping a busy port), `--port 0` (any free port),
`--open` (open the browser when ready), `--dev`, and `--no-frontend`.

## Database & data directory

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_DATABASE_URL` | `sqlite+aiosqlite:///./precursor.db` | Async SQLAlchemy URL. Point at Postgres for production. |
| `PRECURSOR_DATA_DIR` | `.precursor` *(checkout)* / *user data dir* *(installed)* | On-disk working directory. Holds [workspace](/features/workspaces) clones (`workspaces/`), content-addressed [attachment](/features/attachments) blobs (`blobs/`), the self-hosted draw.io editor (`drawio/`), the agents runtime's Copilot home (`agents/copilot-home/`), and the [background app](/features/background-app)'s `runtime.json` + `logs/`. Relative paths resolve against the process working directory. |

The two defaults above depend on **how Precursor was installed**. A source
checkout keeps its state beside the code, so every clone and worktree is an
isolated sandbox. An installed wheel has no such home — and is typically started
by a login item whose working directory is `/` — so it resolves to a per-user
directory instead:

| Platform | User data dir |
| --- | --- |
| macOS | `~/Library/Application Support/Precursor` |
| Linux | `$XDG_DATA_HOME/precursor`, else `~/.local/share/precursor` |
| Windows | `%APPDATA%\Precursor` |

Setting either variable explicitly overrides both behaviours.

```bash
# SQLite (default — no setup)
PRECURSOR_DATABASE_URL=sqlite+aiosqlite:///./precursor.db

# PostgreSQL (needs the `postgres` extra for asyncpg)
PRECURSOR_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/precursor
```

## Background app & updates

Used by the [background app](/features/background-app) — the supervisor, the
menu-bar tray, and the in-place updater.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_UPDATE_REPO` | `lrivallain/precursor` | Repository the update check reads releases from. Point it at a fork to track your own builds. |
| `PRECURSOR_UPDATE_CHANNEL` | *(empty)* | `stable` (tagged releases) or `nightly` (rolling build of `main`). Empty means "match the running build": a dev version follows nightly, a tagged one follows stable. |
| `PRECURSOR_UPDATE_EXTRAS` | `kanban` | Comma-separated extras carried across a self-update, so `precursor service update` doesn't silently drop the plugins you installed with. Add `tray` if you use the menu-bar icon. This is *unioned* with `uv`'s install receipt; prefix a name with `-` (`-kanban`) to drop one instead. |
| `PRECURSOR_UPDATE_CHECK_TTL_SECONDS` | `900` | How long an update-check result is cached before GitHub is asked again. |
| `PRECURSOR_UPDATE_NOTIFY` | `prompt` | What the tray does when a *background* check finds a new build. `prompt` raises a notification with an **Update and restart** button (macOS `osascript`, Linux `notify-send --action`); `notify` is a plain toast; `off` stays quiet and leaves it to the menu's status line. Anything else is read as `prompt`. |

## Diagram editor

`.drawio` files open in a self-hosted draw.io editor. The webapp is downloaded
on demand into `<data_dir>/drawio/<version>/` the first time you open a diagram
— it is deliberately not bundled in the wheel (~53 MB download, ~150 MB on
disk). See [Workspaces → Editing diagrams](/features/workspaces#editing-diagrams).

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_DRAWIO_VERSION` | `v31.3.1` | draw.io release tag to install. Changing it installs the new release on next use and removes the old one. |
| `PRECURSOR_DRAWIO_DOWNLOAD_URL` | GitHub release `draw.war` | Template for the archive URL; `{version}` is substituted. Point it at an internal mirror on an air-gapped network. |

## LLM provider

The provider and its credentials are configured **at runtime** — **Settings →
Model** — not via the environment. The GitHub providers fall back to your
`gh auth login` session when no token is saved. See
[Configuration → Connecting a model](/guide/configuration#connecting-a-model).

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_GITHUB_CLI_USER` | *(empty)* | Which `gh` login supplies the token (`gh auth token --user …`). Set it when several accounts are signed in, so the resolved token doesn't depend on the CLI's active account — see [GitHub authentication](/guide/configuration#several-accounts-signed-in-to-gh). |

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

Independent sweeps bound long-term database growth — see
[Storage & retention](/features/storage) for the full picture and the on-demand
cockpit. Each window is set **at runtime** in an `AppSetting`; every sweep runs on
startup and repeats daily via a background ticker. Only the poll cadence is
env-level.

| Setting | Default | Where | Description |
| --- | --- | --- | --- |
| `tool_result_retention_days` | `0` (keep forever) | Settings → System | Days before a large tool result's content is replaced in place with a short placeholder. |
| `live_transcript_retention_days` | `7` | Settings → Live | Days after a [Live session](/features/live-sessions#transcript-retention) ends before its transcript segments are deleted. `0` keeps them forever. Insights, notes and summary are preserved. |
| `agent_event_retention_days` | `30` | Settings → Agents | Days before an agent's archived timeline events are deleted. `0` keeps them forever. A *running* agent is never pruned, and every agent keeps its result, artifacts and messages. |
| `agent_event_max_per_session` | `2000` | Settings → Agents | Hard ceiling on archived events per agent — only the newest are kept. `0` is unlimited. |

Two levers govern agent timelines because agent traffic is **bursty rather than
aged**: one long autonomous session can add tens of MB in a day, which a time
window alone wouldn't touch for weeks.

Poll cadences (`PRECURSOR_TOOL_RESULT_RETENTION_POLL_SECONDS`,
`PRECURSOR_LIVE_TRANSCRIPT_RETENTION_POLL_SECONDS`,
`PRECURSOR_AGENT_EVENT_RETENTION_POLL_SECONDS`) default to `86400` (daily).
Every sweep is gated by `PRECURSOR_SCHEDULER_ENABLED`.

## Skills directory

The [skills](/features/skills-memory) folder is resolved the way the Copilot CLI
resolves its home: `COPILOT_HOME` → `XDG_CONFIG_HOME/copilot` → `~/.copilot`, with
a `PRECURSOR_SKILLS_DIR` override.

## Agents

[Agents mode](/features/agents-mode) is toggled at runtime (**Settings → Agents**),
which is the only on/off control — there is no env-level default, because
installing the `agents` extra is itself the opt-in. One env var affects *which*
runtime it drives, and the [agent
orchestrator](/features/agents-mode/orchestration) governance adds two
process-level knobs:

| Variable | Default | Description |
| --- | --- | --- |
| `COPILOT_CLI_PATH` | *(empty)* | Read by the Copilot SDK, and the first thing Precursor's runtime probe checks. Point it at a `copilot` binary to pin the runtime; otherwise Precursor falls back to the SDK's download cache and then to `copilot` on `PATH`. The probe never downloads — see [agents mode](/features/agents-mode#pointing-at-a-specific-cli). |
| `PRECURSOR_AGENTS_MAX_CONCURRENT` | `3` | Concurrency governor — the max agents the [orchestrator](/features/agents-mode/orchestration#budgets-the-concurrency-governor) lets execute a turn at once. Extra ready agents queue and are released as slots free up. `0` or negative disables the cap (unbounded). |
| `PRECURSOR_AGENTS_RETRY_BACKOFF_SECONDS` | `60` | Base backoff for [auto-retry](/features/agents-mode/orchestration#retry-auto-recovery) of a failed agent. Delay grows exponentially per attempt (`base × 2ⁿ`); the scheduler re-runs the agent once its retry time is due, up to the agent's `max_retries`. |

Per-agent **token budget** and **max retries** aren't env vars — they're set in
each agent's settings drawer (or baked into a
[blueprint](/features/agents-mode/orchestration#blueprints-reusable-templates)).

## MCP tool servers

Most [MCP](/features/mcp) built-ins are toggled **at runtime** (**Settings →
MCP**). A few env knobs affect the `playwright` and Agent 365 servers:

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_MCP_IDLE_TTL_SECONDS` | `600` | How long an idle MCP client session is kept warm before it's torn down. A later call re-attaches; lower it to hold fewer child processes open. |
| `PRECURSOR_MCP_WARMUP_ENABLED` | `true` | Connect the enabled servers in the background shortly after startup, one at a time, so the first prompt doesn't pay connect + initialize + list_tools for all of them at once. Never blocks startup or a request, and never starts an interactive sign-in. Off means every server connects lazily on first use. |
| `PRECURSOR_MCP_WARMUP_DELAY_SECONDS` | `5` | Grace period before the warm-up touches the first server, leaving startup (schema migration, plugin hydration, the SPA's own first requests) the machine to itself. |
| `PRECURSOR_MCP_WARMUP_GAP_SECONDS` | `1` | Pause between servers during the sweep. Raise it to spread the cost further; the sweep is sequential either way. |
| `PRECURSOR_GITHUB_MCP_TOOLSETS` | `context,repos,issues,pull_requests,users` | Which toolsets the bundled GitHub MCP server registers. Trimming it is the cheapest way to cut that server's schema cost, since tool schemas are re-sent every turn. |
| `PRECURSOR_PLAYWRIGHT_BROWSER` | `msedge` | Browser channel the `playwright` server drives (`--browser`): one of `msedge`, `chromium`, `chrome`, `firefox`, `webkit`, or `default`. Defaults to **Microsoft Edge** so it can ride the corporate SSO/WAM broker for authenticated Entra scraping. Set `chromium` on machines without Edge. `default` **omits `--browser` entirely** — the escape hatch for an `@playwright/mcp` build that predates the flag (fails with `unknown option '--browser'`). Overridable at runtime in **Settings → MCP → Playwright browser** (the DB value wins). |
| `PRECURSOR_WORKIQ_TENANT_ID` | *(empty)* | Microsoft tenant **GUID** the [Agent 365](/features/mcp#agent-365-workiq-teams-and-workiq-user) servers (`workiq-teams`, `workiq-user`, `workiq-planner`, `workiq-word`, `workiq-excel`) address. Overridden by **Settings → MCP → Microsoft 365 tenant**; when both are empty Precursor reads the tenant off an existing WorkIQ sign-in. Entra rejects `common` / `organizations` here — it must be a GUID. |
| `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` | *(empty)* | Browser profile the `playwright` server uses (`--user-data-dir`). Empty means **reuse `@playwright/mcp`'s own shared, machine-wide profile** — so any Entra/SSO sign-in already onboarded there (incl. via other Playwright-MCP tools) carries over. Set a path to pin an isolated profile for Precursor instead. |

### WorkIQ / Agent 365 sign-in

These govern how the Entra credentials behind the WorkIQ and Agent 365 servers
are kept alive. Defaults are tuned for an unattended install — a
[scheduled workflow](/features/workflows/running#triggers-and-scheduling) has
nobody at a browser.

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_WORKIQ_KEEPALIVE_ENABLED` | `true` | Run the background ticker that silently refreshes WorkIQ tokens before they expire. Off means every lapse needs an interactive sign-in. |
| `PRECURSOR_WORKIQ_KEEPALIVE_POLL_SECONDS` | `60` | How often that ticker checks whether a token is near expiry. How far ahead of expiry it renews is **not** configurable: it is a quarter of the token's own lifetime (at least five minutes), which is what decides how many attempts fit before the token dies. `GET /api/mcp/auth/diagnostics` reports the resulting `renewal_lead_seconds` per credential. |
| `PRECURSOR_WORKIQ_SILENT_REAUTH_ENABLED` | `true` | Try the hands-free (no-browser) re-authentication path before falling back to an interactive prompt. |
| `PRECURSOR_WORKIQ_AUTO_REAUTH_ENABLED` | `true` | Automatically start a re-authentication when a credential is found to have lapsed, rather than waiting for you to click the banner. |
| `PRECURSOR_WORKIQ_CHAIN_REAUTH_ENABLED` | `true` | After a WorkIQ / Agent 365 sign-in succeeds, immediately retry the **other** Entra credential on the hands-free silent path while the SSO session is hot, so you get [one prompt instead of one per credential](/features/mcp#signing-in-to-workiq-and-agent-365). Set `false` to renew each credential only when it's independently needed. |
| `PRECURSOR_WORKIQ_LOOPBACK_PORT_FALLBACK` | `true` | When an OAuth sign-in's preferred loopback callback port (`12798` / `12799` / `12800`) is already in use, bind a free **ephemeral** port instead of failing. Entra ignores the port of a loopback redirect for public clients, so this is transparent and lets several windows sign in at once. Set `false` for the strict "port is busy — finish that other sign-in first" behaviour. |
| `PRECURSOR_WORKIQ_KEEPALIVE_IDLE_AFTER_SECONDS` | `21600` (6 h) | Stop keeping a WorkIQ / Agent 365 credential warm once no server using it has been called for this long, so a server you never use doesn't [nag you to sign in](/features/mcp#signing-in-to-workiq-and-agent-365). Usage is tracked per credential and the clock is seeded at startup, so a restart doesn't leave everything cold. `0` disables the back-off and refreshes every signed-in credential indefinitely. |
| `PRECURSOR_WORKIQ_KEEPALIVE_SURFACE_IDLE_LAPSE` | `true` | Let the keep-alive [surface an idle credential's lapse proactively](/features/mcp#signing-in-to-workiq-and-agent-365): once an idle token has genuinely expired it probes once, and if the refresh token is dead it raises the sign-in banner instead of waiting for your next request to stall on the doomed handshake. A still-refreshable idle session recovers silently. Set `false` to keep idle credentials completely quiet until you touch them yourself. |
| `PRECURSOR_WORKIQ_AUTH_LOG_LEVEL` | `debug` | Level of the dedicated **`precursor.mcp.auth`** channel that traces [every decision taken about a WorkIQ credential](/features/mcp#when-a-sign-in-prompt-needs-explaining) — keep-alive verdicts, the Entra `AADSTS…` code that refused a silent refresh, each leg of a re-auth. Independent of `PRECURSOR_LOG_LEVEL`, and verbose by default because a sign-in lapse only happens in the wild and can't be reproduced on demand. The channel is quiet outside an auth episode. Use `info` for transitions only, or `warning` to silence it — `GET /api/mcp/auth/diagnostics` keeps the full in-memory trace either way. |

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
