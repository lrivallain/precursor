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
uvx precursor-ai         # run the latest published wheel, nothing to set up
```

Prefer to keep it around as a tool?

```bash
uv tool install precursor-ai
precursor-ai
```

On startup Precursor prints a banner with the URL to open in your browser.

::: info Package & command names
The PyPI distribution is **`precursor-ai`** (the plain `precursor` name was
already taken). It installs a matching **`precursor-ai`** command — so
`uvx precursor-ai` needs no `--from` — plus a shorter **`precursor`** alias. The
import package is `precursor`.
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

[Agents mode](/features/agents-mode) is **opt-in**: it is *not* installed by the steps
above — it lives behind its own `agents` extra:

```bash
uv sync --extra agents                 # adds github-copilot-sdk on top of dev deps
uv run --extra agents precursor --dev  # …or run the dev stack with it (= make dev)
```

::: warning ~90 MB native runtime
The `github-copilot-sdk` wheel **bundles the native Copilot CLI runtime binary**
(~90 MB download, ~145 MB on disk), which is why it's kept out of the default
install. Installing the extra is the opt-in — agents follow the runtime and come
on once it resolves, with the switch in **Settings → Agents**.
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

## Install as a browser app (PWA)

Precursor ships a [web app manifest](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)
and a service worker, so Chromium browsers (Chrome, Edge) offer to **install it
as a standalone app** — its own window, a dock/taskbar icon, no address bar.
Look for the install icon in the address bar, or the browser menu →
_Install Precursor…_.

Installation only appears in a **one-process production run** (the service
worker is registered in the built SPA, not under `precursor --dev`):

```bash
make build          # build the SPA
uv run precursor    # open http://localhost:8000 and install from the browser
```

::: warning It's a window, not an offline app
The installed app is a convenience wrapper around your **local** Precursor
instance — there is deliberately **no offline caching**. It only works while the
`precursor` process is running, on the same machine, over `localhost`:

- **No offline use.** Every request hits the live FastAPI backend (LLM
  providers, MCP servers, the database, GitHub auth all live server-side). If
  the process isn't running, the window opens but nothing works.
- **Localhost only.** Service workers and the install prompt require a
  [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts).
  `localhost` counts, so same-machine use works over plain HTTP; installing over
  a LAN address or remotely would require serving Precursor behind HTTPS.
- **Not portable / multi-device.** Installing on another device still points at
  the one running instance — it isn't a standalone copy.
- **Platform gaps.** Desktop Chrome/Edge give the best experience; Safari/iOS
  only support a manual _Add to Home Screen_ with stricter limits.
:::

## Next steps

- [Quick start](/guide/quick-start) — create your first topic.
- [Configuration](/guide/configuration) — connect a model and GitHub.
