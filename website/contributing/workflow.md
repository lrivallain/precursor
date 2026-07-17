---
title: Development workflow
---

# Development workflow

## Branch, commit, PR

1. **Open (or claim) an issue** describing the change.
2. **Branch from `main`**: `git checkout -b feat/short-description`.
3. **Keep commits focused.** Conventional commit prefixes (`feat:`, `fix:`,
   `chore:`, `docs:`) are encouraged.
4. **Open a PR** using the template; reference the issue with `Closes #N` when
   applicable.

## Database migrations

Alembic migrations are the single source of truth for the schema. On startup the
app brings the database to `head` automatically (`alembic upgrade head`), so a
fresh database is built from migrations and an existing one is migrated in place —
there is no manual step and no separate dev backfill.

After changing a model, generate the matching migration from the diff and review
it:

```bash
make migration m="add foo to chats"   # autogenerate from the model change
# review the new file under precursor/backend/alembic/versions/, then commit it
make migrate                          # (optional) apply it to your local DB now
```

The migration then applies to dev and prod alike on the next startup. Keep **one
migration per change**. Autogenerate covers most cases — double-check column type
changes, server defaults, and any data migrations by hand.

## Continuous integration

Every PR runs `.github/workflows/ci.yml`:

- **Backend** — `uv sync`, then ruff check, ruff format check, mypy (strict), and
  pytest.
- **Frontend** — `npm ci`, then typecheck and build.

All jobs must pass before merge. Run `make check` locally first to catch failures
early.

## Adding a plugin

Plugins live in their own packages and register via
`[project.entry-points."precursor.plugins"]`. See the
[plugin reference](/reference/plugins).

## Documentation

- In-repo docs live under `docs/` and the top-level markdown files
  (`README.md`, `CONTRIBUTING.md`, …).
- This showcase + docs **site** lives under `website/` (VitePress) and is
  published to GitHub Pages automatically on push to `main` via
  `.github/workflows/pages.yml`.

To work on the site locally:

```bash
cd website
npm install
npm run docs:dev        # live-reload dev server
npm run docs:build      # production build → website/.vitepress/dist
```
