---
title: Technical stack
---

# Technical stack

One Python process serves the JSON API and the pre-built React SPA. **uv**
handles env, run, build and release.

| Layer | Technology |
| --- | --- |
| **Tooling** | [uv](https://docs.astral.sh/uv/) (env, run, build, release); `make` targets wrap it |
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2, sse-starlette |
| **LLM** | the `openai` SDK, pointed at the active provider (Copilot / Azure / any OpenAI-compatible gateway) |
| **MCP** | the `mcp` Python SDK, client and server (MCP 2: `>=2.2,<3`, with `httpx2`) |
| **Frontend** | Vite, React 19, TypeScript (strict), Tailwind CSS, Lucide icons; Monaco + monaco-yaml for the Files editor |
| **DB** | SQLite (`aiosqlite`) by default, PostgreSQL (`asyncpg`, `postgres` extra) |
| **Desktop** | `pystray` + `Pillow` (`tray` extra); launchd / systemd user units / Windows `Run` entries for login items |
| **Docs** | VitePress (`website/`), also bundled in the wheel and served at `/docs/` |

## Backend

- `from __future__ import annotations` everywhere; async wherever DB or network
  is touched (`AsyncSession` via `Depends(get_session)`).
- Pydantic schemas in `schemas/` are the single source of truth for API shapes;
  read models never embed secrets.
- Alembic owns the schema: `alembic upgrade head` runs on startup.
- `get_settings()` (cached) covers process-level `.env` values; everything
  editable in the app is an `AppSetting` row (see
  [Configuration](/reference/configuration)).

Runtime dependency floors live in `pyproject.toml`.

## Frontend

- Built to `frontend/dist` and **bundled inside the wheel**; no Node.js at
  runtime.
- All HTTP goes through `src/lib/api.ts`; chat streams use a manual SSE reader
  (`src/lib/sse.ts`) because they POST a JSON body.
- API types are **generated** from the Pydantic schemas into
  `src/lib/generated/apiTypes.ts` (`make api-types`) and aliased in
  `src/lib/types.ts`.
- Theme tokens are CSS variables; dark mode is `.dark` on `<html>`.
- Monaco is a lazily loaded chunk; only JSON and YAML get a language service.
- Lint with oxlint, unit tests with Vitest (`*.test.ts` next to the module).

## Build, versioning and CI

- Version is **CalVer** (`YYYY.M.MICRO`) from git tags via **hatch-vcs**; no
  literal to edit. Read it from `precursor.__version__` or `GET /api/version`.
- `hatch_build.py` bundles the built SPA and docs into the wheel only for real
  (non-editable) builds, so `uv sync`, dev and CI need no frontend build.
- **CI** (`ci.yml`, every PR and `main` push): lockfile check, quality ratchet
  and API-types check, ruff, mypy (strict), pytest (Linux and Windows), a fresh
  wheel install, a Windows install journey, frontend typecheck/tests/build, and
  the docs build.
- **Releases** are automatic: a stable release every night from green `main`, and
  a rolling `nightly` prerelease after each green `main` CI. See
  [Releasing](/contributing/releasing).

See the [architecture](/reference/architecture) for how the pieces fit at
runtime.
