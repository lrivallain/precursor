---
title: Installation
---

# Installation

Precursor uses **[uv](https://docs.astral.sh/uv/)** for everything Python —
environment, running, building, and releasing. Pick the option that matches what
you want: keep it running all the time, run it occasionally, or develop it.

## Option A — install it as a background app (recommended)

One command installs Precursor, registers it to start when you log in, and
starts it now:

```bash
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

After that, the app is managed rather than launched:

```bash
precursor service status    # where it's running
precursor service update    # newest build + restart
precursor tray              # menu-bar icon
```

See [Background app](/features/background-app) for the whole surface — the login
item, the menu-bar control, update channels, and where the data lives.

::: info Channels
The script installs the **nightly** build (a rolling prerelease of `main`) by
default. For tagged releases only, prefix it with `PRECURSOR_CHANNEL=stable`.
:::

## Option B — run a published build (zero setup)

The published wheel bundles the pre-built SPA, so an installed package is
completely self-contained:

```bash
uvx "precursor-ai[kanban]"   # run the latest published wheel, nothing to set up
```

Prefer to keep it around as a tool?

```bash
uv tool install "precursor-ai[kanban]"
precursor-ai
```

On startup Precursor prints a banner with the URL to open in your browser.

::: info Package & command names
The PyPI distribution is **`precursor-ai`** (the plain `precursor` name was
already taken). It installs a matching **`precursor-ai`** command plus a shorter
**`precursor`** alias. The import package is `precursor`.
:::

::: info Optional extras
Some features ship as separate [plugins](/features/plugins) so they aren't forced
on every install. `[kanban]` pulls in the
[GitHub Projects board](/features/kanban); drop it (`uvx precursor-ai`) for a
lean core with no board. `[agents]` adds
[Agents mode](/features/agents-mode) — which then provisions a ~90 MB native
runtime, so it stays opt-in.
`[tray]` adds the [menu-bar control](/features/background-app#the-menu-bar-icon).
Combine them: `uvx "precursor-ai[kanban,agents]"`.
:::

::: info Requirements
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** (it manages
  the Python interpreter for you).
- The *production* runtime needs **no Node.js** — the SPA is pre-built inside the
  wheel.
:::

## Option C — from source (for development)

Clone the repository and let `uv` and `npm` set up both halves of the stack:

```bash
git clone https://github.com/lrivallain/precursor.git
cd precursor

make sync                      # uv sync + npm --prefix frontend install
cp .env.example .env
```

`uv sync` installs the in-repo plugins under `plugins/` as workspace members, so
a source checkout has the kanban board wired in from the start.

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
Vite. Only the single-process *production* run is Node-free.
:::

### Run the dev stack

One command starts uvicorn with `--reload` **and** the Vite dev server with HMR
(Ctrl-C stops both):

```bash
uv run precursor --dev
# or:  make dev
```

In `--dev`, the port you pass is the **UI** (Vite), and Vite proxies `/api` to
the backend on a hidden port (`--port` + 1 by default):

```bash
uv run precursor --dev --port 9000    # open :9000 (UI); API on :9001 behind it
uv run precursor --port 8100 --open   # prod-style single process, opens browser
```

Running several instances at once? Pick a different `--port` per instance — a
busy port automatically bumps to the next free one (`--strict-port` fails
instead, `--port 0` grabs any free port).

### One-process production run

For a single-process run, build the SPA first so FastAPI can serve it:

```bash
make build                     # npm --prefix frontend run build → frontend/dist
uv run precursor               # serves API + SPA on :8000
```

## Optional: Agents mode

[Agents mode](/features/agents-mode) is **opt-in**: it is *not* installed by the
steps above — it lives behind its own `agents` extra:

```bash
uv sync --extra agents                 # adds github-copilot-sdk on top of dev deps
uv run --extra agents precursor --dev  # …or run the dev stack with it (= make dev)
```

::: warning ~90 MB native runtime
The SDK wheel itself is small, but Agents mode needs the **native Copilot CLI**
(~90 MB, ~145 MB on disk). The SDK downloads it on first use, or reuses a
system-wide `copilot` install — which is why the extra is kept out of the default
install. Installing it is the opt-in — agents follow the runtime and come on once
it resolves, with the switch in **Settings → Agents**.
:::

### Pointing at a specific CLI

Precursor resolves the runtime **read-only**, so rendering Settings never pulls
a binary. It takes the first of:

1. `COPILOT_CLI_PATH`, if it points at an existing file.
2. The SDK's own download cache (`~/Library/Caches/github-copilot-sdk` on macOS,
   `~/.cache/github-copilot-sdk` on Linux).
3. A `copilot` executable on `PATH` — a Homebrew, npm or installer-provisioned
   CLI counts.

Whatever it finds is handed to the SDK, so the runtime always drives the binary
**Settings → Agents** reports. If none resolve, install the
[Copilot CLI](https://github.com/github/copilot-cli) or set `COPILOT_CLI_PATH`.

## Automatic upgrades on startup

When you pull new code or upgrade Precursor, both the **frontend** (rebuilt if
`frontend/dist` is missing or stale) and the **database** (Alembic
`upgrade head`) are brought up to date automatically on the next start:

```bash
git pull
uv run precursor               # frontend built + DB migrated automatically
```

## Install as a browser app (PWA)

Precursor ships a web app manifest and a service worker, so Chromium browsers
offer to **install it as a standalone app** — its own window, a dock/taskbar
icon, no address bar. Look for the install icon in the address bar, or the
browser menu → _Install Precursor…_.

Installation only appears in a **one-process production run**, since the service
worker is registered in the built SPA rather than under `precursor --dev`:

```bash
make build          # build the SPA
uv run precursor    # open http://localhost:8000 and install from the browser
```

::: warning It's a window, not an offline app
The installed app is a convenience wrapper around your **local** Precursor
instance — there is deliberately **no offline caching**. It only works while the
`precursor` process is running, on the same machine, over `localhost` (which
counts as a secure context, so plain HTTP is fine). Installing it on another
device still points at the one running instance; it isn't a standalone copy.
:::

## Next steps

- [Quick start](/guide/quick-start) — create your first topic.
- [Configuration](/guide/configuration) — connect a model and GitHub.
