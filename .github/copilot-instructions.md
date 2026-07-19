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

### Tooling

- **uv** is the Python toolchain — env, run, build, release. Prefer
  `uv run <cmd>` (e.g. `uv run pytest`, `uv run ruff check .`) over a manually
  activated venv, and `uv build` for wheels. `make` targets wrap these
  (`make sync`, `make dev`, `make check`, `make wheel`). Don't reintroduce
  `pip install` / bare `uvicorn` invocations in docs or scripts.

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
2. When models change, generate a migration with `make migration m="…"`
   (Alembic autogenerate), review it, and commit it. Alembic is the single
   source of truth: `init_db` runs `alembic upgrade head` on startup for dev and
   prod alike — there is no `create_all` or hand-written dev backfill.
3. Keep comments to "why", not "what". Don't add docstrings to code you didn't
   touch.
4. **Assess documentation impact** — every user-facing change must update the
   docs in the same PR (see [Documentation](#documentation) below). Treat "docs
   updated" as part of the definition of done, not a follow-up.
5. Run the quality gates (`make check`, listed in `CONTRIBUTING.md`).

## Documentation

**Documentation is part of every change, not an afterthought.** Before you call
a change complete, explicitly assess what documentation it affects and update it
in the *same* PR. If you conclude nothing needs changing, say so in the PR
description so the assessment is visible.

### Where documentation lives

- **`README.md`** — the project's front door (motto, quick start, highlights).
- **`docs/`** — in-repo deep dives (`architecture.md`, `plugins.md`, …).
- **`website/`** — the VitePress showcase + docs site published to GitHub Pages
  (`.github/workflows/pages.yml`, base path `/precursor/`):
  - `website/index.md` — landing page hero + **feature grid**.
  - `website/guide/` — getting started (installation, quick start, configuration).
  - `website/features/` — **one page per feature**.
  - `website/reference/` — stack, architecture, configuration, API, plugins.
  - `website/contributing/` — contribution, workflow, releasing.
  - `website/.vitepress/config.mts` — nav + sidebar registration.
  - `website/public/screenshots/` — product screenshots (see below).
- **`CHANGELOG.md`** — keep the `[Unreleased]` section current for any
  user-facing change.

### Decision checklist — what triggers a doc update

| If your change… | …then update |
| --- | --- |
| Adds or changes a **user-facing feature** | the relevant `website/features/*.md` page (+ landing feature grid in `index.md` if it's a headline capability), and `CHANGELOG.md` `[Unreleased]` |
| Adds a **new sidebar section / cockpit** | a new `website/features/<section>.md` **and** its sidebar entry in `config.mts` **and** the landing grid — mirroring the in-app wiring (`Sidebar.tsx`, `sections.ts`, and the **command palette**) |
| Adds/renames/removes a **setting or env var** | `website/guide/configuration.md` and `website/reference/configuration.md` (and `.env.example` if env-level) |
| Adds/changes a **slash command** | the surface's feature page (e.g. `/reminder` on `features/scheduler.md`) and `frontend/src/lib/commands.ts` stays the source of truth |
| Changes the **API** (routes, schemas, SSE events) | `website/reference/api.md` and the architecture request-flow notes |
| Adds a **built-in MCP server / provider** | `website/features/mcp.md` / `website/reference/architecture.md` |
| Changes **installation / run** steps | `README.md`, `website/guide/installation.md`, `CONTRIBUTING.md` |
| Is **experimental / untested** | mark it clearly as WIP (e.g. a `::: warning` admonition), don't present it as stable |

### Exposing a new feature on the site

1. Write `website/features/<feature>.md` (mirror the tone of the existing pages:
   a one-line intent, a `<Screenshot>` if it has UI, then how it works).
2. Register it in the sidebar (`website/.vitepress/config.mts`) and, for a
   headline capability, add a card to the feature grid in `website/index.md`.
3. Cross-link from related pages (e.g. a scheduler feature links to MCP guards).
4. Add a `CHANGELOG.md` `[Unreleased]` entry.

### Screenshots

Screenshots live in `website/public/screenshots/` and are **theme-aware**: each
has a light file (`foo.png`) and a dark file (`foo-dark.png`); the `<Screenshot>`
component derives the `-dark` variant and swaps by site theme. **When a UI change
alters a screenshotted screen, retake both variants.**

Capture rules (keep them consistent and privacy-safe):

- Run a **seeded demo instance** with the **account hidden** — no resolvable
  GitHub token, so the persona shows "Guest / Not connected" (never a real
  account/avatar). Reuse the demo fixtures (the `precursor-demo` repo + Project
  board) rather than real data.
- **Fake missing config** (e.g. a dummy Speech key, disabled remote MCP servers)
  so shots have **no error/warning banners** — never use real secrets.
- Capture **both** `colorScheme: light` and `dark` at `deviceScaleFactor: 2`,
  writing `foo.png` and `foo-dark.png`.
- **Clip** to the relevant pane when the sidebar/persona footer would leak the
  account.
- Reference the new file from a page via
  `<Screenshot src="/screenshots/foo.png" alt="…" caption="…" />` (light path
  only — the component finds the dark one).

### Verify before you finish

- Build the site: `cd website && npm run docs:build` — it must pass (checks
  broken component usage and, effectively, that referenced pages exist).
- Sanity-check new nav/sidebar links resolve and every referenced screenshot
  (both variants) exists.

## What *not* to do

- Don't add new top-level dependencies without flagging it in the PR.
- Don't introduce a Node.js runtime requirement in production — the SPA must
  be pre-built and served by FastAPI.
- Don't echo secret values (API tokens) in API responses. Use the
  `api_keys_present` boolean pattern from `schemas/settings.py`.
- Don't hardcode the version anywhere — it's CalVer from git tags via hatch-vcs
  (read `precursor.__version__`). See `RELEASING.md`.
- Don't add unrelated refactors to a feature PR.
