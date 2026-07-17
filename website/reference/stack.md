---
title: Technical stack
---

# Technical stack

Precursor is a single Python service that serves a JSON API and a pre-built React
SPA from the same process. One toolchain — **uv** — covers env, run, build, and
release.

## At a glance

| Layer | Technology |
| --- | --- |
| **Tooling** | [uv](https://docs.astral.sh/uv/) for env, run, build & release |
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, sse-starlette |
| **LLM** | the `openai` SDK, pointed at the active provider (Copilot / GitHub Models / Azure / any OpenAI-compatible gateway) |
| **MCP** | the `mcp` Python SDK (client + server) |
| **Frontend** | Vite + React 19 + TypeScript, Tailwind CSS, Lucide icons |
| **DB** | SQLite for dev (`aiosqlite`), PostgreSQL for prod (`asyncpg`, extra) |
| **Docs** | Markdown in-repo; this site is built with VitePress |

## Backend

- **Python 3.12+**, with `from __future__ import annotations` throughout.
- **FastAPI** exposes the JSON API under `/api/*` and mounts the built SPA at `/`.
- **Async everywhere** DB or network is touched — `AsyncSession` from
  `precursor.backend.db` via `Depends(get_session)`.
- **Pydantic v2** models in `schemas/` — read models never embed secrets.
- **SQLAlchemy 2 (async)** ORM; **Alembic** is the single source of truth for the
  schema (`alembic upgrade head` runs on startup).
- **sse-starlette** streams chat replies over Server-Sent Events.
- **Settings** come from a cached `get_settings()`; runtime-editable settings
  layer over env defaults via `AppSetting` rows.

Key dependencies (floors, from `pyproject.toml`): `fastapi`, `uvicorn[standard]`,
`sqlalchemy[asyncio]`, `aiosqlite`, `alembic`, `pydantic`, `pydantic-settings`,
`sse-starlette`, `httpx`, `openai`, `mcp`, `python-multipart`, `pypdf`, `pyyaml`.

## Frontend

- **Vite + React 19 + TypeScript** (strict mode), built to `frontend/dist` and
  **bundled inside the wheel** so an installed package is self-contained.
- **Tailwind CSS** with **CSS-variable theme tokens**; dark mode toggles by adding
  `.dark` to `<html>`.
- All HTTP goes through `src/lib/api.ts`; streaming chat uses a manual SSE reader
  (`src/lib/sse.ts`) because it POSTs a JSON body (not `EventSource`).
- Function components with **named exports**; TS types in `src/lib/types.ts`
  mirror the Pydantic schemas.

## Build, versioning & packaging

- **uv** is the toolchain; `pyproject.toml` is a uv project with a committed
  `uv.lock`.
- Version is **CalVer** (`YYYY.M.MICRO`) derived from git tags by **hatch-vcs** at
  build time — there is no literal to edit (`precursor.__version__`), and it's
  exposed at `GET /api/version`.
- A conditional build hook (`hatch_build.py`) bundles the built SPA into the wheel
  only for real (non-editable) builds, so `uv sync` / dev / CI never need a
  frontend build.
- **CI** runs ruff, ruff format, mypy (strict), pytest, and the frontend
  typecheck + build on every PR. A `v*` tag push builds the wheel and publishes a
  GitHub Release.

See the [architecture](/reference/architecture) for how the pieces fit together
at runtime.
