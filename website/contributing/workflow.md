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

**Documentation is part of every change.** When you add or change a user-facing
feature, update the docs in the *same* PR — don't defer it. Use the decision
checklist in
[`.github/copilot-instructions.md`](https://github.com/lrivallain/precursor/blob/main/.github/copilot-instructions.md#documentation)
to decide what to touch (a feature page, the landing grid, configuration
reference, `CHANGELOG.md`, screenshots, …), and keep the `[Unreleased]` section
of `CHANGELOG.md` current.

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

The site is also served **in-app** at `/docs/`. `precursor --dev` starts a live
VitePress server automatically (the SPA proxies `/docs` to it, so edits
hot-reload); for the one-port build, `make docs` builds it with base `/docs/` and
`make wheel` bundles it into the wheel. The base is set via the `DOCS_BASE` env
var (`/docs/` in-app; default `/` for GitHub Pages), so the public site is
unaffected. See [Serving the docs in-app](/reference/api#serving-the-docs-in-app).

### Screenshots

Screenshots in `website/public/screenshots/` are **theme-aware** — each has a
light file (`foo.png`) and a dark file (`foo-dark.png`), and the `<Screenshot>`
component swaps them by site theme. If a UI change alters a screenshotted screen,
**retake both variants** from a seeded demo instance **with the account hidden**
(no resolvable token → "Guest") and **no config warnings** (fake missing config
rather than using real secrets). Capture light + dark at 2× and clip out the
persona footer when it would leak the account.
