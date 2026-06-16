# Changelog

All notable changes to Precursor are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Precursor uses **CalVer** (`YYYY.M.MICRO`); the version is derived from the
latest git tag (`v<version>`) by hatch-vcs at build time. See
[RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- CalVer versioning derived from git tags (hatch-vcs); a single source of truth
  replaces the previously hardcoded version literals.
- `GET /api/version` endpoint and a version line in the Settings panel.
- `version` field on `GET /api/health`.
- CI workflow (lint, format, type-check, tests, frontend build) on PRs and
  pushes to `main`.
- Release workflow: pushing a `v*` tag builds the wheel + sdist and publishes a
  GitHub Release with auto-generated notes.
- `SECURITY.md` (threat model + private vulnerability reporting) and a
  Security & deployment section in the README documenting the single-user,
  local-first, no-auth model.
- Dependabot config for pip, npm, and GitHub Actions.
- Contributor prompt helpers (`.github/prompts/`): `/ship-change` and
  `/release` workflows.
- The build-in command panels (`/notes`, GitHub draft cards) now use a shared
  `ResizableTextarea` with a draggable bottom edge; the chosen height is
  remembered per panel across sessions, giving a larger editing area.

### Changed

- `ruff` now ignores `B008` (FastAPI `Depends()` idiom); the lint gate passes
  clean across the repo.
- `mypy precursor` passes under `strict` and is a hard CI gate.
- **uv** is the documented tool for the Python env, running, building, and
  releasing (README, CONTRIBUTING, Makefile, RELEASING).
- The built wheel is now **self-contained**: a conditional build hook
  (`hatch_build.py`) bundles the SPA inside the package for distribution builds
  (not editable installs), so `uvx precursor` / `uv tool install precursor`
  serve the UI with no extra files.
- `.env.example` LLM section reconciled with `config.py` (lists all three
  providers; default `github_copilot`).
- Docs: rewrote `docs/architecture.md` to current state (scheduler, workspaces,
  command-runner jail, skills/memory, real MCP transports, three LLM providers);
  clarified GitHub token resolution (`GITHUB_TOKEN` → `gh` CLI → mock) and the
  dev-vs-prod port model (Vite `:5173` proxy → backend `:8000`).
- Frontend dependencies upgraded (Vite 8, TypeScript 6, `@vitejs/plugin-react`
  6, `react-markdown` 10, `lucide-react` 1). `lucide-react` 1 removed brand
  icons, so the GitHub mark now ships as a local `GithubIcon` component.
- Migrated the frontend to **Tailwind CSS v4** (CSS-first config): theme tokens
  moved into an `@theme` block in `index.css`, dark mode via `@custom-variant`,
  and the `@tailwindcss/vite` plugin replaces the PostCSS setup (no more
  `postcss.config.js` / `tailwind.config.js`).
- Dependabot now groups only minor/patch bumps; majors get their own PR so a
  breaking upgrade (e.g. Tailwind v4) is never bundled with safe ones.
- Unified backend logging: a single `logging.config.dictConfig` (applied at
  startup and passed to uvicorn as `log_config`) gives every record — app,
  uvicorn, and third-party (httpx, mcp, watchfiles) — one human format with an
  ISO-8601 UTC timestamp, level, and logger name. Modules now use
  `getLogger(__name__)` (no hardcoded `precursor.*` names) and operational
  `print()` calls became logger calls. App `debug` stays app-only: noisy
  libraries (aiosqlite, SQLAlchemy, sse-starlette, …) are pinned to fixed levels
  so turning on app DEBUG doesn't unleash per-statement library spam. Output is
  ANSI-coloured when stderr is a TTY and plain when piped/redirected. The
  in-tree stdio MCP servers (fetch / workspace-fs / cmd-runner / precursor)
  apply the same config in their entrypoints, so their `mcp.server` logs share
  the format instead of FastMCP's timestamp-less default; routine `mcp.client`
  connection chatter (session IDs, protocol negotiation) is quieted to WARNING.

### Fixed

- `precursor --dev` no longer prints a burst of Vite `http proxy error …
  ECONNREFUSED 127.0.0.1:8000` on startup: the Vite dev server now launches only
  once the backend port is accepting connections, instead of racing it.

- Scheduled topics now actually run: the background scheduler is started (and
  stopped) with the app lifespan. It was constructed but never started, so no
  schedule ever fired and "Run now" was a no-op.
- The `schedules` router is now registered, so `PATCH /api/schedules/{id}`
  (Save) and `POST /api/schedules/{id}/run` (Run now) work instead of returning
  `405 Method Not Allowed` (the requests were falling through to the SPA
  catch-all).

<!--
Release sections are added below by the release process, newest first, e.g.:

## [2026.6.0] - 2026-06-15

### Added
### Changed
### Fixed
### Removed
-->
