.PHONY: help hooks sync dev backend frontend docs build plugins-build wheel check lockcheck test migration migrate

# Lockfiles are resolved by CI, never locally: a corporate package mirror
# rewrites artifact URLs and weakens their integrity metadata. UV_FROZEN keeps
# every `uv` call below from silently re-resolving. Override deliberately
# (`make sync UV_FROZEN=0`) only when you intend to change the lockfile.
export UV_FROZEN ?= 1

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

hooks:  ## Install the git hooks (blocks proxy-polluted lockfiles)
	git config core.hooksPath .githooks
	@echo "Hooks enabled from .githooks/"

# uv is the single source for the Python env, running, and building.
# The `dev` dependency group is included by uv automatically, so `uv sync` /
# `uv run` always carry the tooling (ruff/pytest/mypy) — no `--extra dev`.
# `npm ci` (not `install`) installs *from* the lockfile without rewriting it.
sync:  ## Install/refresh the dev environment (uv + npm)
	uv sync
	npm --prefix frontend ci
	npm --prefix website ci

# Full dev stack: uvicorn --reload + Vite HMR (Ctrl-C stops both). `--extra
# agents` pulls the Copilot SDK so Agents mode is live (opt-in payload, kept out
# of `make sync`/CI). Drop it if you don't need Agents mode.
dev:  ## Run the full dev stack (API + Vite HMR, with Agents mode)
	uv run --extra agents precursor --dev

# Backend only (uvicorn --reload, no Vite).
backend:  ## Run the backend only (uvicorn --reload, with Agents mode)
	uv run --extra agents precursor --dev --no-frontend

# Vite dev server only.
frontend:  ## Run the Vite dev server only
	npm --prefix frontend run dev

# Build the SPA so a plain `uv run precursor` can serve it on one port.
build: plugins-build  ## Build the SPA into frontend/dist (+ in-repo plugin UIs)
	npm --prefix frontend run build

# In-repo plugin frontends. Each builds with the host's toolchain into its own
# Python package (plugins/<dist>/src/<module>/web), so it rides along in the
# wheel and Precursor serves it at /api/plugins/<id>/assets/*. Add a plugin by
# adding its distribution name here.
PLUGINS ?= precursor-kanban

plugins-build:  ## Build every in-repo plugin's frontend into its package
	@for p in $(PLUGINS); do \
		echo "building $$p frontend"; \
		PRECURSOR_PLUGIN=$$p npm --prefix frontend exec -- vite build \
			--config frontend/vite.plugin.config.ts || exit 1; \
	done

# Build the docs with base /docs/ so the app serves them in-app at /docs/.
# (GitHub Pages builds the same source with the default base "/" separately.)
docs:  ## Build the VitePress docs for in-app serving (base /docs/)
	DOCS_BASE=/docs/ npm --prefix website run docs:build

# Build the self-contained wheel + sdist (SPA + docs bundled inside the package),
# plus a wheel per in-repo plugin (each carrying its own built frontend).
wheel: build docs  ## Build the distributable wheels + sdists (uv, all packages)
	uv build --all-packages

# Quality gates — mirrors CI (.github/workflows/ci.yml).
check: lockcheck  ## Run all backend + frontend quality gates
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy precursor plugins/precursor-kanban/src
	uv run pytest -q
	npm --prefix frontend run typecheck
	npm --prefix frontend run build
	$(MAKE) plugins-build

lockcheck:  ## Verify lockfiles pin public artifacts with strong hashes
	python3 scripts/check_lockfiles.py

test:  ## Run the backend test suite (uv)
	uv run pytest -q

# Autogenerate a migration from model changes (brings the local DB to head
# first so the diff is correct). Usage: make migration m="add foo to chats".
migration:  ## Autogenerate a migration from model changes (m="description")
	@test -n "$(m)" || { echo 'usage: make migration m="description"'; exit 1; }
	uv run alembic upgrade head
	uv run alembic revision --autogenerate -m "$(m)"

# Apply pending migrations to the configured database.
migrate:  ## Apply pending migrations (alembic upgrade head)
	uv run alembic upgrade head
