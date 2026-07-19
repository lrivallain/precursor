---
title: Installation
---

# Installation

Precursor uses **[uv](https://docs.astral.sh/uv/)** for everything Python —
environment, running, building, and releasing. There are two ways to install it:
grab a published build to just *use* it, or clone the repo to *develop* it.

## Option A — run a published build (zero setup)

The published wheel bundles the pre-built SPA, so an installed package is
completely self-contained. If you have `uv` installed, you can run the latest
release without cloning anything:

```bash
uvx --from precursor-ai precursor    # run the latest published wheel, nothing to set up
```

Prefer to keep it around as a tool?

```bash
uv tool install precursor-ai
precursor
```

On startup Precursor prints a banner with the URL to open in your browser.

::: info Package vs. command name
The PyPI distribution is **`precursor-ai`** (the plain `precursor` name was already
taken), but the command it installs is still **`precursor`**. That's why `uvx`
needs `--from precursor-ai precursor`, while `pip install precursor-ai` /
`uv tool install precursor-ai` then give you a `precursor` command.
:::

::: info Requirements
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** (it manages
  the Python interpreter for you).
- The *production* runtime needs **no Node.js** — the SPA is pre-built inside the
  wheel.
:::

## Option B — from source (for development)

Clone the repository and let `uv` and `npm` set up both halves of the stack:

```bash
git clone https://github.com/lrivallain/precursor.git
cd precursor

make sync                      # uv sync + npm --prefix frontend install
cp .env.example .env
```

<details>
<summary>Without <code>make</code></summary>

```bash
uv sync                        # backend: .venv + Python deps
npm --prefix frontend install  # frontend: Vite + React toolchain (needs Node.js)
cp .env.example .env
```

</details>

::: warning The dev server needs Node.js
`precursor --dev` and the SPA build (`make build`) need **Node.js + npm** for
Vite. Only the single-process *production* run is Node-free. `make sync` runs
both install steps in one go.
:::

### Run the dev stack

One command starts uvicorn with `--reload` **and** the Vite dev server with HMR
(Ctrl-C stops both):

```bash
uv run precursor --dev
# or:  make dev
```

In `--dev`, the port you pass is the **UI** (Vite), and Vite proxies `/api` to
the backend, which sits on a hidden port (`--port` + 1 by default):

```bash
uv run precursor --dev --port 9000    # open :9000 (UI); API on :9001 behind it
uv run precursor --port 8100 --open   # prod-style single process, opens browser
```

Running several instances at once? Just pick a different `--port` per instance —
a busy port automatically bumps to the next free one (pass `--strict-port` to
fail instead, or `--port 0` to grab any free port).

### One-process production run

For a single-process run, build the SPA first so FastAPI can serve it:

```bash
make build                     # npm --prefix frontend run build → frontend/dist
uv run precursor               # serves API + SPA on :8000
```

## Optional: Agents mode

[Agents mode](/features/agents) is **opt-in and off by default**. It is *not*
installed by the steps above — it lives behind its own `agents` extra:

```bash
uv sync --extra agents                 # adds github-copilot-sdk on top of dev deps
uv run --extra agents precursor --dev  # …or run the dev stack with it (= make dev)
```

::: warning ~90 MB native runtime
The `github-copilot-sdk` wheel **bundles the native Copilot CLI runtime binary**
(~90 MB download, ~145 MB on disk), which is why it's kept out of the default
install. Installing the extra only makes the runtime *available* — agents stay
disabled until you turn them on in **Settings → Agents**.
:::

## Automatic upgrades on startup

When you pull new code or upgrade Precursor, both the **frontend** and the
**database** are brought up to date automatically on the next start — no manual
build or migration step:

- **Frontend** — rebuilt if `frontend/dist` is missing or stale.
- **Database** — migrations applied during startup via Alembic
  (`alembic upgrade head`).

```bash
git pull
uv run precursor               # frontend built + DB migrated automatically
```

## Next steps

- [Quick start](/guide/quick-start) — create your first topic.
- [Configuration](/guide/configuration) — connect a model and GitHub.
