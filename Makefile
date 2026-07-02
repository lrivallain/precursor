.PHONY: help sync dev backend frontend build wheel check test migration migrate

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

# uv is the single source for the Python env, running, and building.
# The `dev` dependency group is included by uv automatically, so `uv sync` /
# `uv run` always carry the tooling (ruff/pytest/mypy) — no `--extra dev`.
sync:  ## Install/refresh the dev environment (uv + npm)
	uv sync
	npm --prefix frontend install

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
build:  ## Build the SPA into frontend/dist
	npm --prefix frontend run build

# Build the self-contained wheel + sdist (SPA bundled inside the package).
wheel: build  ## Build the distributable wheel + sdist (uv)
	uv build

# Quality gates — mirrors CI (.github/workflows/ci.yml).
check:  ## Run all backend + frontend quality gates
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy precursor
	uv run pytest -q
	npm --prefix frontend run typecheck
	npm --prefix frontend run build

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
