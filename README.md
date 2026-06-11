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
| Backend  | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, sse-starlette   |
| LLM      | `openai` SDK pointed at `https://models.github.ai/inference`          |
| MCP      | `mcp` Python SDK (client + server scaffolding)                        |
| Frontend | Vite + React 19 + TypeScript, Tailwind CSS 3, Lucide React            |
| DB       | SQLite for dev (`aiosqlite`), PostgreSQL for prod (`asyncpg`, extra)  |

## Quick start

### 1. Backend

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Add GITHUB_TOKEN with the `models:read` fine-grained permission to your .env.

uvicorn precursor.backend.main:app --reload
```

Without `GITHUB_TOKEN`, Precursor automatically uses the `MockProvider` so the
chat flow stays usable.

### 2. Frontend (dev)

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to :8000
```

### 3. Frontend (prod build, served by FastAPI)

```bash
cd frontend
npm install
npm run build    # outputs to frontend/dist
```

Restart uvicorn and the SPA is mounted at `/`.

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
│   └── vite.config.ts
├── pyproject.toml
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

## Documentation

- [Architecture](docs/architecture.md)
- [Plugin system](docs/plugins.md)
- [Contributing](CONTRIBUTING.md)

## License

[MIT](LICENSE)
