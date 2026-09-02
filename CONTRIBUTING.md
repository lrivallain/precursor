# Contributing to Precursor

Thanks for your interest in improving Precursor. This project is small and
opinionated — issues and small focused PRs are the easiest way to land changes.

## Getting set up

> Only want to *use* Precursor? The
> [one-command install](https://precursor.vuptime.io/guide/installation) is much
> shorter — a source checkout is the contributor path and additionally needs
> Node.js for the frontend toolchain.

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

### Working on the background app

`precursor service …` and `precursor tray` (see
[docs/architecture.md](docs/architecture.md#supervised-background-instance)) also
work from a checkout, and deliberately keep a checkout's paths: the supervised
instance anchors at the repo root, so it uses the same `./precursor.db` and
`.precursor/` as `uv run precursor`. Running `precursor service start` in a
worktree therefore supervises *that* worktree.

The menu-bar icon needs the GUI extra; without it the tray tests skip rather
than fail:

```bash
uv sync --extra tray
uv run precursor tray
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

The suite is hermetic: the root `conftest.py` points the app at a throwaway
database, skills and data directory, and keeps the LLM provider on the offline
`MockProvider` by hiding any GitHub token from it. So `make check` behaves the
same whether or not you're signed in to `gh` — a test that needs model output
injects its own fake provider rather than calling one. It lives at the
repository root, not under `tests/`, so the whole suite gets the same isolation.

## Plugins

Anything that isn't "topics, chat, GitHub" belongs in a plugin rather than in
core — see [docs/plugins.md](docs/plugins.md) for the contract.

Plugins live **outside this repository**. A plugin is an ordinary Python
distribution that registers a `precursor.plugins` entry point, ships its own
built frontend inside its wheel, and releases on its own cadence;
[`precursor-kanban`](https://github.com/lrivallain/precursor-kanban) is the
reference implementation. Install one into your dev environment the same way a
user would:

```bash
uv pip install precursor-kanban
```

Core's side of that seam — the registry, entry-point discovery, router mounting
and the typed `precursor.plugin_api` surface — is what this repository owns and
tests. `tests/test_plugins.py` covers it with a stub plugin, so the suite never
depends on a particular distribution being installed.

Ship a plugin to users as an optional extra on `precursor-ai` and add its test
directory to `testpaths`. Adding or removing a workspace member changes
`uv.lock`, which CI regenerates — see below.

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

### If `npm ci` can't find a version

Corporate mirrors lag the public registries, so a lockfile CI just produced may
pin a version your mirror hasn't cached — `npm ci` then fails with a 404 for one
package. Install without consulting the lockfile:

```bash
npm --prefix website install --no-package-lock
```

That resolves against whatever your mirror does have and, because there is no
lockfile to write, leaves the committed one untouched. Your `node_modules` may
differ slightly from CI's, which is fine for local work — CI still builds from
the real lockfile.

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

### Listing one in the catalogue

The plugin catalogue is `website/plugins/`: **one markdown file per plugin**,
whose YAML frontmatter is the metadata Precursor reads and whose body is the
documentation page published at `/plugins/<id>`. Adding an entry is adding that
one file — the catalogue page, the sidebar and the in-app **Settings → Plugins →
Available** list are all generated from it, and the build hook ships the
directory inside the wheel as `precursor/catalog`.

`tests/test_plugin_catalog.py` validates every shipped entry, so a malformed
submission fails CI. In particular `distribution` must be a **bare PyPI name** —
it is handed to an installer, so anything expressing a location is refused.

The submitter-facing checklist lives at
[`website/plugins/submitting.md`](website/plugins/submitting.md).

## Reporting security issues

Please **don't** open public issues for security reports. Email the maintainers
listed in the repo `CODEOWNERS` (when present) or use GitHub's private
vulnerability reporting.
