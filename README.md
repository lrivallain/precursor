# Precursor

> Opinionated approach to work follow-up, built as an AI assistant.

Precursor is a small AI assistant, built with an opinionated approach to tracking
work-in-progress conversations alongside the issues they belong to. Every chat is
scoped to a
**topic** that can be linked to (or create) a GitHub issue; the assistant uses
the issue body, comments, and labels as live context so newer updates outweigh
older ones.

## Quick start

**One prerequisite, one command.** [uv](https://docs.astral.sh/uv/) brings its
own Python, so it's the only thing to install first
([instructions](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

That installs Precursor, registers it to **start when you log in**, and starts it
now. No clone, no Node.js, no build step, no database to create — the published
package already carries the interface, and the schema is created on first start.
Open the URL it prints (<http://localhost:8000> by default) and you're in.

From then on it's managed rather than launched:

```bash
precursor service status    # is it up, and on which port
precursor service update    # newest build + restart
precursor tray              # menu-bar control
```

See [Installation](https://precursor.vuptime.io/guide/installation) for the other
ways to install (try it with `uvx`, no login item, Windows, stable channel) and
[Background app](https://precursor.vuptime.io/features/background-app) for the
whole service surface.

**GitHub credentials (optional).** Precursor resolves a GitHub token in this
order: (1) a token saved in **Settings → GitHub**, then (2) your **GitHub CLI**
session (`gh auth token`) if you're signed in via `gh auth login`. So if you
already use `gh`, you don't need to set anything. If several accounts are signed
in, set `PRECURSOR_GITHUB_CLI_USER=<login>` so the token doesn't depend on
whoever last ran `gh auth switch`. A token needs the `models:read` fine-grained
permission (or Copilot access) for real model responses. With **no** token at
all, Precursor falls back to the `MockProvider` so the chat flow stays usable
offline.

## Highlights

- Collapsible, searchable, **tree-organized** topic sidebar
- Each topic optionally linked to a GitHub issue; issue labels tag the chat
- Multi-turn chat with **SSE streaming** and markdown rendering
- Powered by **GitHub Copilot** (OpenAI-compatible) with a mock provider for
  offline development
- **MCP both ways**: Precursor exposes its conversations as an MCP server *and*
  attaches external MCP tool servers per topic
- **Agents mode** (opt-in): hand long-running tasks to an autonomous Copilot
  SDK agent attached to a topic/chat, followed in a workflow-style tab. Nothing
  to install — the native runtime it drives is one click in **Settings → Agents**
- **Workflows** (opt-in): chain agents into a reusable, named pipeline that a
  coordinator runs unattended — with quality gates, human approval checkpoints,
  and schedule/webhook triggers
- Single uvicorn process in production — FastAPI serves the API and mounts the
  built React SPA
- **Runs in the background**: one-command install, a login item, a menu-bar icon
  showing whether it's up, and `precursor service update` to pull the newest
  build and restart
- **Plugin-ready**: backend entry points + a frontend extension registry,
  designed for things like a future drawio preview/generator

## Stack

| Layer    | Tech                                                                  |
| -------- | --------------------------------------------------------------------- |
| Tooling  | [uv](https://docs.astral.sh/uv/) for env, run, build & release        |
| Backend  | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, sse-starlette   |
| LLM      | `openai` SDK pointed at `https://api.githubcopilot.com`               |
| MCP      | `mcp` Python SDK (client + server scaffolding)                        |
| Frontend | Vite + React 19 + TypeScript, Tailwind CSS 3, Lucide React            |
| DB       | SQLite for dev (`aiosqlite`), PostgreSQL for prod (`asyncpg`, extra)  |

## Working on Precursor

A source checkout is the **contributor** path — it additionally needs Node.js for
the frontend toolchain:

```bash
git clone https://github.com/lrivallain/precursor.git && cd precursor
make sync                     # uv sync + npm --prefix frontend install
cp .env.example .env
uv run precursor --dev        # uvicorn --reload + Vite HMR (Ctrl-C stops both)
```

After a `git pull`, the next start rebuilds a stale frontend and runs the Alembic
migrations on its own. See [CONTRIBUTING.md](CONTRIBUTING.md) for the quality
gates and the rest of the dev stack.

## Project layout

```
precursor/
├── precursor/backend/
│   ├── main.py             # FastAPI app + SPA mount + lifespan
│   ├── config.py           # pydantic-settings
│   ├── db.py               # async SQLAlchemy engine + session
│   ├── models/             # Topic, Message, AppSetting
│   ├── schemas/            # Pydantic request/response models
│   ├── routers/            # topics, chat (SSE), settings, github, mcp
│   ├── services/
│   │   ├── llm/            # provider protocol + GH Models + mock
│   │   ├── github_client.py
│   │   └── mcp/            # server + client manager
│   ├── plugins/            # entry-point loader + registry
│   └── alembic/            # migrations
├── frontend/
│   ├── src/components/     # Sidebar, ChatPanel, SettingsPanel, MessageBubble
│   ├── src/lib/            # api, sse, plugins, theme, types
│   └── vite.config.ts      # built to frontend/dist → bundled into the wheel
├── pyproject.toml          # uv project: deps, build (hatch-vcs CalVer), tooling
├── uv.lock                 # uv-managed lockfile (committed)
├── Makefile                # uv-based dev/build shortcuts
└── alembic.ini
```

## Design principles

- **Streaming-first** chat with tool-call visualization
- **Single process** in production — no Node.js runtime required
- **Each topic is an independent conversation context**, hydrated from its
  linked GitHub issue (newer comments preferred over older)
- **Extensible by design**: see [docs/plugins.md](docs/plugins.md) for the
  plugin contract — third parties can mount routers, contribute frontend
  panels, and register MCP tools without forking the core

## Security & deployment model

> [!IMPORTANT]
> Precursor is designed as a **single-user, local-first** app and ships with
> **no authentication**. Run it bound to `127.0.0.1` (the default) and do not
> expose it to a network or the public internet without putting your own
> authenticating reverse proxy in front of it.

Specific things to keep local:

- The API and SPA have **no auth** — anyone who can reach the port has full
  access to your topics, settings, and stored tokens.
- The optional **command-runner** MCP tool can execute shell/Python/Node. Keep
  the Docker "jail" enabled; disabling it grants full local-disk access.
- The built-in **MCP-over-HTTP** transport is off by default and only binds to
  loopback — leave it that way unless you front it with auth.
- Secrets (the GitHub token, LLM provider keys) live in the local DB (set via
  Settings) and are never echoed by the API. Don't commit `.env`.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Versioning & releases

Precursor uses **CalVer** (`YYYY.M.MICRO`, e.g. `2026.6.0`). The version is a
single source of truth derived from git tags by hatch-vcs at build time — there
is no literal to edit. The running version is exposed at `GET /api/version` and
shown in the Settings panel.

Releases ship from a pushed `v<version>` tag via GitHub Actions. See
[RELEASING.md](RELEASING.md) and [CHANGELOG.md](CHANGELOG.md).

## Documentation

📖 **[Website & full documentation](https://precursor.vuptime.io/)** —
feature guides, installation, configuration, architecture, and contribution
docs, published from [`website/`](website/) on every push.

In-repo references:

- [Architecture](docs/architecture.md)
- [Plugin system](docs/plugins.md)
- [Contributing](CONTRIBUTING.md)
- [Releasing](RELEASING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
