# Contributing to Precursor

Thanks for your interest in improving Precursor. This project is small and
opinionated — issues and small focused PRs are the easiest way to land changes.

## Getting set up

Precursor uses **[uv](https://docs.astral.sh/uv/)** for the Python toolchain
(env, run, build, release). Install it once, then:

```bash
make sync                 # uv sync --extra dev + npm install
cp .env.example .env
```

<details>
<summary>Without make</summary>

```bash
uv sync --extra dev
cp .env.example .env
cd frontend && npm install && cd ..
```
</details>

Run the dev stack (uvicorn `--reload` + Vite HMR, both stop on Ctrl-C):

```bash
make dev
# or:  uv run precursor --dev
```

Other launch options:

```bash
uv run precursor                  # single process: API + pre-built SPA on one port
uv run precursor --dev --no-frontend   # backend only (uvicorn --reload)
npm --prefix frontend run dev          # Vite only
```

For a one-process production run, build the SPA first so FastAPI can serve it:

```bash
make build        # npm --prefix frontend run build
uv run precursor  # serves API + SPA on :8000
```


## Quality gates

Before opening a PR, run the full gate set (mirrors CI):

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

## Versioning & releases

Precursor uses **CalVer** (`YYYY.M.MICRO`). The version is derived from git
tags by hatch-vcs — **never edit a version literal**; there isn't one. Cutting a
release is a tag push; see [RELEASING.md](RELEASING.md). Keep the `[Unreleased]`
section of [CHANGELOG.md](CHANGELOG.md) up to date in your PR when the change is
user-facing.

## Workflow

1. Open (or claim) an issue describing the change.
2. Branch from `main`: `git checkout -b feat/short-description`.
3. Keep commits focused; conventional commit prefixes (`feat:`, `fix:`,
   `chore:`, `docs:`) are encouraged.
4. Open a PR using the template; reference the issue with `Closes #N` when
   applicable.

## Code style

- **Python**: ruff config in `pyproject.toml` (line length 100, target 3.12).
  Type-annotate public surfaces; rely on `from __future__ import annotations`.
- **TypeScript**: strict mode is on. Prefer named exports for components,
  function components only. Tailwind classes for styling; CSS variables for
  theme tokens (see `frontend/src/index.css`).
- **Comments**: only where the *why* isn't obvious. The codebase favors small,
  self-explanatory units over heavy docstrings.

## Adding a plugin

See [docs/plugins.md](docs/plugins.md). Plugins live in their own packages and
register via `[project.entry-points."precursor.plugins"]`.

## Reporting security issues

Please **don't** open public issues for security reports. Email the maintainers
listed in the repo `CODEOWNERS` (when present) or use GitHub's private
vulnerability reporting.
