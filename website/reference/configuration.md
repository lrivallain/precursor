---
title: Configuration reference
---

# Configuration reference

Most configuration happens **at runtime in the app** (see the
[Configuration guide](/guide/configuration)). This page documents the
**process-level** settings that live in `.env` — every one has a built-in default,
so the file is optional.

Copy `.env.example` to `.env` and uncomment only what you want to override.

## Server

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_HOST` | `127.0.0.1` | Bind address. Keep it on loopback unless you front the app with your own auth. |
| `PRECURSOR_PORT` | `8000` | The URL you open. In `--dev`, the Vite UI runs here and the backend moves to `PORT + 1`. A busy port auto-bumps to the next free one. |
| `PRECURSOR_LOG_LEVEL` | `info` | uvicorn/app log level. |
| `PRECURSOR_SHUTDOWN_GRACE_SECONDS` | `3` | Seconds to wait for in-flight requests (e.g. SSE streams) before force-closing on Ctrl-C, so the port is released promptly. |

CLI flags mirror several of these: `--port`, `--api-port`, `--host`,
`--strict-port` (fail instead of bumping a busy port), `--port 0` (any free port),
`--open` (open the browser when ready), `--dev`, and `--no-frontend`.

## Database

| Variable | Default | Description |
| --- | --- | --- |
| `PRECURSOR_DATABASE_URL` | `sqlite+aiosqlite:///./precursor.db` | Async SQLAlchemy URL. Point at Postgres for production. |

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

## Skills directory

The [skills](/features/skills-memory) folder is resolved the way the Copilot CLI
resolves its home: `COPILOT_HOME` → `XDG_CONFIG_HOME/copilot` → `~/.copilot`, with
a `PRECURSOR_SKILLS_DIR` override.

::: tip Runtime settings win
Runtime settings layer over env defaults: each setting resolves as "env / `.env`
default, overridden by an `AppSetting` row if present, clamped to a sane range".
Don't read `os.environ` directly — the app resolves settings through
`services/app_settings.py`.
:::
