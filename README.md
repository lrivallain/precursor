# Precursor

> Per-topic AI chat interface for work follow-up, where each topic maps to a GitHub issue.

Precursor is a small, opinionated assistant for tracking work-in-progress
conversations alongside the issues they belong to. Every chat is scoped to a
**topic** that can be linked to (or create) a GitHub issue; the assistant uses
the issue body, comments, and labels as live context so newer updates outweigh
older ones.

## Highlights

- Collapsible, searchable, **tree-organized** topic sidebar
- Each topic optionally linked to a GitHub issue; issue labels tag the chat
- Multi-turn chat with **SSE streaming** and markdown rendering
- Powered by **GitHub Models** (OpenAI-compatible) with a mock provider for
  offline development
- **MCP both ways**: Precursor exposes its conversations as an MCP server *and*
  attaches external MCP tool servers per topic
- Single uvicorn process in production — FastAPI serves the API and mounts the
  built React SPA
- **Plugin-ready**: backend entry points + a frontend extension registry,
  designed for things like a future drawio preview/generator

## Stack

| Layer    | Tech                                                                  |
| -------- | --------------------------------------------------------------------- |
| Tooling  | [uv](https://docs.astral.sh/uv/) for env, run, build & release        |
| Backend  | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, sse-starlette   |
| LLM      | `openai` SDK pointed at `https://models.github.ai/inference`          |
| MCP      | `mcp` Python SDK (client + server scaffolding)                        |
| Frontend | Vite + React 19 + TypeScript, Tailwind CSS 3, Lucide React            |
| DB       | SQLite for dev (`aiosqlite`), PostgreSQL for prod (`asyncpg`, extra)  |

## Quick start

Precursor uses **[uv](https://docs.astral.sh/uv/)** for everything Python —
environment, running, building, and releasing. Install it once
([instructions](https://docs.astral.sh/uv/getting-started/installation/)), then:

```bash
uv sync --extra dev          # create .venv + install (uv manages the interpreter)
cp .env.example .env
# Add GITHUB_TOKEN with the `models:read` fine-grained permission to your .env.
```

Without `GITHUB_TOKEN`, Precursor automatically uses the `MockProvider` so the
chat flow stays usable.

### Run it (one command)

```bash
uv run precursor --dev        # uvicorn --reload + Vite HMR (Ctrl-C stops both)
# or:  make dev
```

`uv run` resolves the project's environment on the fly — no manual activation.
Other entry points:

```bash
uv run precursor                     # single process: API + pre-built SPA on one port
uv run precursor --dev --no-frontend # backend only (uvicorn --reload)
npm --prefix frontend run dev        # Vite only
```

### Frontend prod build (served by FastAPI)

```bash
make build                    # npm --prefix frontend run build → frontend/dist
uv run precursor              # serves API + SPA on :8000
```

The single-process run needs the SPA pre-built; `uv run precursor` then serves
it from `frontend/dist`. The SPA is also bundled **inside the wheel**, so an
installed build is self-contained:

```bash
uvx precursor                 # run the latest published wheel, zero setup
# or pin it:  uv tool install precursor && precursor
```

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
- Secrets (`GITHUB_TOKEN`, provider keys) live in `.env` / the local DB and are
  never echoed by the API. Don't commit `.env`.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Versioning & releases

Precursor uses **CalVer** (`YYYY.M.MICRO`, e.g. `2026.6.0`). The version is a
single source of truth derived from git tags by hatch-vcs at build time — there
is no literal to edit. The running version is exposed at `GET /api/version` and
shown in the Settings panel.

Releases ship from a pushed `v<version>` tag via GitHub Actions. See
[RELEASING.md](RELEASING.md) and [CHANGELOG.md](CHANGELOG.md).

## Documentation

- [Architecture](docs/architecture.md)
- [Plugin system](docs/plugins.md)
- [Contributing](CONTRIBUTING.md)
- [Releasing](RELEASING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
