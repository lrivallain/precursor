# Architecture

Precursor is a single-process Python service that serves a JSON API and the
built React SPA from the same uvicorn worker. There is no Node.js runtime in
production.

```mermaid
flowchart LR
    subgraph Browser
      SPA[React SPA]
    end

    subgraph Process[uvicorn worker]
      FAPI[FastAPI app]
      DB[(SQLite / Postgres)]
      LLM[LLM provider\n(GH Models / mock)]
      MCPS[MCP Server\n(exposes conversations)]
      MCPC[MCP Client manager\n(attaches tool servers)]
      PLG[Plugin registry]
    end

    GH[GitHub REST API]
    EXT_MCP[External MCP servers]
    EXT_CLI[CLI agents / IDE extensions]

    SPA -- "/api/*" --> FAPI
    SPA <-- "SSE stream" --- FAPI
    FAPI --> DB
    FAPI --> LLM
    FAPI --> GH
    FAPI --> MCPC --> EXT_MCP
    EXT_CLI --> MCPS --> FAPI
    PLG --> FAPI
```

## Request flow: streamed chat

1. `POST /api/topics/{id}/messages/stream` with the user prompt.
2. The router persists the user `Message`, then snapshots history and builds a
   system prompt that includes the linked GitHub issue body + most-recent
   comments + labels.
3. The configured `LLMProvider` yields text deltas, forwarded as SSE
   `delta` events.
4. On stream end, the assistant turn is persisted (using a fresh DB session so
   the response generator is not bound to the request scope).

## Database

- Models live in `precursor/backend/models/`.
- `Topic` self-references for a tree (parent/children).
- `Message` is per-topic with cascade delete.
- `AppSetting` is a JSON-encoded key/value store for runtime-editable settings
  (theme, model, MCP toggles, opaque secrets that are never echoed back).
- Dev startup runs `Base.metadata.create_all`; production uses Alembic.

## GitHub integration

`services/github_client.py` wraps just the endpoints the app needs (list/get
issues, list comments, list labels, create issue). Topic context is rebuilt
on every turn so changes to the linked issue propagate instantly.

## LLM provider abstraction

`services/llm/base.py` defines a tiny protocol:

```python
class LLMProvider(Protocol):
    name: str
    async def stream_chat(self, *, model: str, messages: Sequence[ChatMessage]) -> AsyncIterator[str]: ...
```

Two implementations ship:

- `GitHubModelsProvider` — official, uses the `openai` SDK against
  `https://models.github.ai/inference`.
- `MockProvider` — deterministic streamed reply, used automatically when no
  `GITHUB_TOKEN` is set.

Adding a new provider (Azure OpenAI, local Ollama, ...) is one file plus a
branch in `get_llm_provider()`.

## MCP

Precursor is *both* an MCP server and an MCP client:

- **As server** (`services/mcp/server.py`) — exposes `list_topics`, `get_topic`,
  `post_message` so external agents can drive conversations.
- **As client** (`services/mcp/client.py`) — keeps a registry of external tool
  servers users can attach per-topic.

The current code ships the descriptor / registry layer; transport wiring is the
next step and lives behind these stable surfaces.

## SPA

- Vite + React 19 + Tailwind.
- Theming via CSS variables (`light` / `dark` / `system`), toggled by adding
  `.dark` to `<html>`.
- The SPA fetches `/api/plugins` on boot — extensions describe themselves
  declaratively (kind + slot + config) and are rendered by renderers
  registered through `src/lib/plugins.ts`.

## Plugin contract

See [plugins.md](plugins.md).
