---
title: Contribution guide
---

# Contributing to Precursor

Thanks for your interest in improving Precursor. The project is small and
opinionated — issues and small, focused PRs are the easiest way to land changes.

## Getting set up

A source checkout is the **contributor** path — if you only want to *use*
Precursor, the [one-command install](/guide/installation) is a lot shorter.

Precursor uses **[uv](https://docs.astral.sh/uv/)** for the Python toolchain
(env, run, build, release) and **Node.js** for the frontend toolchain. Install
both once, then:

```bash
git clone https://github.com/lrivallain/precursor.git
cd precursor

make sync                 # uv sync + npm --prefix frontend install
cp .env.example .env
```

<details>
<summary>Without <code>make</code></summary>

```bash
uv sync
cp .env.example .env
cd frontend && npm install && cd ..
```

</details>

Run the dev stack (uvicorn `--reload` + Vite HMR, both stop on Ctrl-C):

```bash
make dev
# or:  uv run precursor --dev
```

In `--dev`, the port you pass is the **UI** (Vite), and Vite proxies `/api` to
the backend on a hidden port (`--port` + 1 by default). A busy port bumps to the
next free one, so several checkouts can run side by side.

Other launch options:

```bash
uv run precursor --dev --port 9000     # open :9000 (UI); API on :9001 behind it
uv run precursor --dev --no-frontend   # backend only (uvicorn --reload)
npm --prefix frontend run dev          # Vite only
```

For a one-process production run, build the SPA first so FastAPI can serve it:

```bash
make build                # npm --prefix frontend run build → frontend/dist
uv run precursor          # serves API + SPA on :8000
```

::: tip Upgrades are automatic
After a `git pull`, the next `uv run precursor` rebuilds the frontend if
`frontend/dist` is stale and runs Alembic `upgrade head` on the database — no
manual build or migration step.
:::

Plugins are separate distributions with their own release cadence, so a source
checkout starts without them. Add one the way an end user would:

```bash
uv pip install precursor-kanban
```

## Quality gates

Before opening a PR, run the full gate set (it mirrors CI):

```bash
make check
```

<details>
<summary>Individual commands</summary>

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy precursor
uv run pytest

npm --prefix frontend run typecheck
npm --prefix frontend run build
```

</details>

All of these run in CI (`.github/workflows/ci.yml`) on every PR and must pass.

## Code style

- **Python** — ruff config in `pyproject.toml` (line length 100, target 3.12).
  Type-annotate public surfaces; rely on `from __future__ import annotations`.
- **TypeScript** — strict mode is on. Prefer **named exports** and function
  components only. Tailwind classes for styling; **CSS variables** for theme
  tokens (`frontend/src/index.css`).
- **Comments** — only where the *why* isn't obvious. The codebase favors small,
  self-explanatory units over heavy docstrings.

## When you change the API

1. Update **both** the Pydantic schema **and** the TS `types.ts` for any API
   change — they mirror each other.
2. When models change, generate and review a migration — see
   [database migrations](/contributing/workflow#database-migrations).
3. Keep the `[Unreleased]` section of `CHANGELOG.md` up to date when the change is
   user-facing.

## What *not* to do

- Don't add new top-level dependencies without flagging it in the PR.
- Don't introduce a Node.js runtime requirement in production — the SPA must be
  pre-built and served by FastAPI.
- Don't echo secret values (API tokens) in API responses — use the
  `*_present` boolean pattern.
- Don't hardcode the version anywhere — it's CalVer from git tags via hatch-vcs.
- Don't add unrelated refactors to a feature PR.
- Don't grow core to host a single use case — write a [plugin](/reference/plugins)
  instead.

## Next

- [Development workflow](/contributing/workflow) — branches, commits, and PRs.
- [Releasing](/contributing/releasing) — how a version ships.
