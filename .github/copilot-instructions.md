# Copilot / agent instructions for Precursor

Use these notes to stay aligned with the project's conventions when working in
this repo.

## Architecture in one paragraph

Single uvicorn process. FastAPI exposes a JSON API under `/api/*` and mounts
the built React SPA at `/`. SQLAlchemy 2 (async) backs `Topic`, `Message`, and
`AppSetting`. Chat replies stream from an LLM provider (`GitHubModelsProvider`
or `MockProvider` fallback) over Server-Sent Events. Precursor is both an MCP
server (exposes conversations) and an MCP client (attaches tool servers).
Third parties extend the system via the `precursor.plugins` entry-point group
plus a frontend extension registry.

## Conventions

### Python (backend)

- Target **Python 3.12+**. Always use `from __future__ import annotations`.
- Async everywhere DB or network is touched. Use `AsyncSession` from
  `precursor.backend.db` via `Depends(get_session)`.
- Pydantic v2 models in `schemas/`. Never return ORM objects with secrets
  embedded — define a read model.
- Settings come from `precursor.backend.config.get_settings()` (cached).
  Don't read `os.environ` directly in routers.
- Routers are thin; logic goes in `services/`.
- When streaming responses, persist the assistant turn in a **fresh session**
  (`async with SessionLocal() as ...`) because the request-scoped one may be
  closed by the time the generator finishes — see `routers/chat.py`.

### TypeScript (frontend)

- React 19 + Tailwind. Components are function components with named exports.
- Styling uses CSS variables for theme tokens; toggle dark mode by adding
  `.dark` on `<html>` (see `lib/theme.ts`).
- All HTTP goes through `src/lib/api.ts`. Streaming chat goes through
  `src/lib/sse.ts` (we don't use `EventSource` because we POST a JSON body).
- Keep type definitions in `src/lib/types.ts` mirrored to the Pydantic schemas.

### Plugins

- Backend plugins register via `[project.entry-points."precursor.plugins"]`
  and a `register(registry)` callable.
- Frontend extensions are *descriptors* served from `/api/plugins`; the SPA
  matches them to renderers registered through `frontend/src/lib/plugins.ts`.
- Don't grow core to host a single use case — write a plugin instead.

## When making changes

1. Update both the Pydantic schema **and** the TS `types.ts` for any API
   change.
2. Add or update an Alembic migration when models change (dev uses
   `create_all`, prod uses migrations).
3. Keep comments to "why", not "what". Don't add docstrings to code you didn't
   touch.
4. Run the quality gates listed in `CONTRIBUTING.md`.

## What *not* to do

- Don't add new top-level dependencies without flagging it in the PR.
- Don't introduce a Node.js runtime requirement in production — the SPA must
  be pre-built and served by FastAPI.
- Don't echo secret values (API tokens) in API responses. Use the
  `api_keys_present` boolean pattern from `schemas/settings.py`.
- Don't add unrelated refactors to a feature PR.
