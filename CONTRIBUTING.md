# Contributing to Precursor

Thanks for your interest in improving Precursor. This project is small and
opinionated — issues and small focused PRs are the easiest way to land changes.

## Getting set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

cd frontend && npm install && cd ..
```

Run the dev stack:

```bash
# terminal 1 — API
uvicorn precursor.backend.main:app --reload

# terminal 2 — Vite
npm --prefix frontend run dev
```

## Quality gates

Before opening a PR, please run:

```bash
ruff check .
ruff format --check .
mypy precursor
pytest

npm --prefix frontend run typecheck
npm --prefix frontend run build
```

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
