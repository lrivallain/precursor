# Contributing to Precursor

Thanks for your interest in improving Precursor. This project is small and
opinionated — issues and small focused PRs are the easiest way to land changes.

## Getting set up

Precursor uses **[uv](https://docs.astral.sh/uv/)** for the Python toolchain
(env, run, build, release). Install it once, then:

```bash
make sync                 # uv sync + npm ci
cp .env.example .env
```

<details>
<summary>Without make</summary>

```bash
export UV_FROZEN=1        # see "Lockfiles" below
uv sync
cp .env.example .env
cd frontend && npm ci && cd ..
```
</details>

Run the dev stack (uvicorn `--reload` + Vite HMR, both stop on Ctrl-C):

```bash
make dev
# or:  uv run --extra agents precursor --dev   (drop --extra agents to skip Agents mode)
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

## Lockfiles

`uv.lock` and the two `package-lock.json` files are committed, and they must
pin **public** artifacts (`files.pythonhosted.org`, `registry.npmjs.org`) with
strong hashes. **Regenerating them is CI's job, not yours.**

This matters because many managed devices route uv and npm through a corporate
package mirror. Re-resolving there doesn't just relabel URLs, it *weakens* the
lockfile: npm integrity comes back as `sha1` instead of `sha512`, and uv drops
the `size`/`upload-time` provenance. In a diff that looks like a harmless URL
change. Rewriting the URLs back by hand is worse still — it pairs a public
artifact with the weakened metadata.

Enable the guard once, and it will stop you committing one by accident:

```bash
make hooks        # git config core.hooksPath .githooks
make lockcheck    # or check the working tree on demand
```

Day to day, install *from* the lockfiles rather than re-resolving:

```bash
make sync         # uv sync + npm ci  (never `npm install`)
```

The Makefile exports `UV_FROZEN=1`, which matters more than it looks: every
`uv run` re-locks by default, so `make dev`, `make check`, and `make test` would
each rewrite `uv.lock`. If you run `uv` outside make, set it yourself:

```bash
export UV_FROZEN=1
```

**To change a dependency**, edit `pyproject.toml` or `package.json`, commit that,
then let a clean runner resolve it:

```bash
gh workflow run relock.yml --ref "$(git branch --show-current)"
```

The [`Relock`](https://github.com/lrivallain/precursor/actions/workflows/relock.yml)
workflow regenerates the lockfiles, verifies they install *and* build, then
pushes the result back to your branch (or opens a PR when run against `main`).
Dependabot updates arrive the same way. If you only need the packages locally
before that lands, `UV_FROZEN=0 uv lock && uv sync && git restore uv.lock` gets
you a working environment without committing the pollution.

## Database migrations

Alembic migrations are the single source of truth for the schema. On startup the
app brings the database to `head` automatically (`alembic upgrade head`), so a
fresh database is built from migrations and an existing one is migrated in place
— there is no manual step and no separate dev backfill.

After changing a model, generate the matching migration from the diff and review
it:

```bash
make migration m="add foo to chats"   # autogenerate from the model change
# review the new file under precursor/backend/alembic/versions/, then commit it
make migrate                          # (optional) apply it to your local DB now
```

The migration then applies to dev and prod alike on the next startup. Keep one
migration per change. Autogenerate covers most cases — double-check column type
changes, server defaults, and any data migrations by hand.

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
