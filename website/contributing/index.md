---
title: Contribution guide
---

# Contributing to Precursor

Issues and small, focused PRs land best. The canonical rules are in the
repository:
[`CONTRIBUTING.md`](https://github.com/lrivallain/precursor/blob/main/CONTRIBUTING.md)
(setup, gates, lockfiles, tests) and
[`ARCHITECTURE.md`](https://github.com/lrivallain/precursor/blob/main/ARCHITECTURE.md)
(where code goes, building blocks to reuse, size and complexity limits). This
page is the short version.

## Setup

Only want to *use* Precursor? Use the [one-command install](/guide/installation).
A checkout needs **[uv](https://docs.astral.sh/uv/)** and **Node.js 24 LTS**
(or 22.12+).

```bash
git clone https://github.com/lrivallain/precursor.git
cd precursor
make sync                 # uv sync + npm ci (frontend and website)
make hooks                # once: lockfile guard + file-size ratchet in pre-commit
cp .env.example .env
make dev                  # uvicorn --reload + Vite HMR; Ctrl-C stops both
```

| Command | Runs |
| --- | --- |
| `uv run precursor --dev --port 9000` | UI on :9000, API on a hidden port behind Vite (`--port` + 1 by default; busy ports bump) |
| `uv run precursor --dev --no-frontend` | backend only |
| `npm --prefix frontend run dev` | Vite only |
| `make build && uv run precursor` | one process, API + built SPA on :8000 |

Run `uv` outside `make` with `UV_FROZEN=1` (the Makefile sets it), or it
re-locks. Plugins aren't in a checkout; add one with
`uv pip install precursor-kanban`.

::: tip Upgrades are automatic
After a `git pull`, `uv run precursor` rebuilds a stale `frontend/dist` and runs
`alembic upgrade head`.
:::

## Definition of done

```bash
make check
```

It mirrors CI: lockfile check, quality ratchet, API types up to date, ruff
(lint + format), mypy, pytest, frontend typecheck, Vitest unit tests, frontend
build. Also:

- **New logic has tests**: `tests/` for the backend, `*.test.ts` next to the
  module for the frontend. The suite is hermetic (throwaway DB, offline
  `MockProvider`).
- **Stay under the [limits](https://github.com/lrivallain/precursor/blob/main/ARCHITECTURE.md#limits-and-the-quality-ratchet)**:
  800-line files, complexity 15, no new 8+ line duplicates. `make ratchet` fails
  on regressions; after an improvement, run `make ratchet-update` and commit
  the baseline.
- **API change**: update the Pydantic schema (with a `response_model`), run
  `make api-types` and commit `frontend/src/lib/generated/apiTypes.ts`. Never
  edit that file by hand.
- **Model change**: add a [migration](/contributing/workflow#database-migrations).
- **User-facing change**: update the docs and `CHANGELOG.md` `[Unreleased]` in
  the same PR (see [Documentation](/contributing/workflow#documentation)).

## Code style

- **Python**: ruff (line length 100, target 3.12), `from __future__ import
  annotations`, async for DB and network, thin routers with logic in
  `services/`.
- **TypeScript**: strict, function components, named exports, Tailwind with CSS
  variable tokens, `data-tooltip` instead of `title`.
- **Comments** explain *why*, not *what*.

## Don't

- add top-level dependencies without flagging them in the PR;
- require Node.js at runtime (the SPA is pre-built);
- echo secrets in API responses (use `*_present` booleans);
- hardcode the version (CalVer from git tags);
- mix unrelated refactors into a feature PR;
- grow core for a single use case: write a [plugin](/reference/plugins).

## Next

- [Development workflow](/contributing/workflow): branches, migrations, CI,
  lockfiles, docs.
- [Releasing](/contributing/releasing): how a version ships.
