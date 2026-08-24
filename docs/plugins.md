# Plugins

Precursor is intentionally small. Anything that is not part of "topics, chat,
GitHub" should live in a plugin.

A plugin is **one Python package** that can bring three things:

| Contribution | How | Where it shows up |
| --- | --- | --- |
| **Backend** | `registry.add_router(...)` | FastAPI routes under `/api/<plugin>` |
| **UI** | `registry.add_section(...)` + a built ES module in the wheel | A whole section: sidebar rail, home card, ⌘K entry, route at `/<id>` |
| **Tools** | `registry.add_mcp_server(...)` | The MCP catalogue, with the same toggles as core's built-ins |

Install the package, restart, and all three appear. Uninstall it and they all
go. The **kanban board** (`plugins/precursor-kanban/`) is the reference
implementation — read it alongside this document.

## Anatomy

```
my_plugin/
  pyproject.toml               # entry point in the `precursor.plugins` group
  src/my_pkg/
    plugin.py                  # register(registry)
    router.py                  # FastAPI routes
    mcp_server.py              # optional MCP tools (stdio subprocess)
    web/                       # BUILT frontend, served from the wheel
      index.js
  web/src/index.tsx            # frontend source: calls registerSection()
```

## Backend

Import from `precursor.plugin_api` — that module is the surface we keep stable,
unlike `precursor.backend.*`, which is internal and moves freely.

```toml
# pyproject.toml
[project.entry-points."precursor.plugins"]
my_plugin = "my_pkg.plugin:register"
```

```python
# my_pkg/plugin.py
from fastapi import APIRouter
from precursor.plugin_api import PluginRegistry

router = APIRouter(prefix="/api/my-plugin", tags=["my-plugin"])


@router.get("/ping")
async def ping():
    return {"ok": True}


def register(registry: PluginRegistry) -> None:
    registry.add_router(router)
    registry.add_section(id="my-plugin", title="My section")
    registry.add_mcp_server(name="tools", module="my_pkg.mcp_server")
```

Contributions are attributed automatically — the registry knows which plugin is
registering, so ids and MCP server names are namespaced for you and a stray call
outside `register()` raises rather than landing anonymously.

Discovery runs **once per process**, during `create_app()` — deliberately before
the SPA catch-all, since routes match in registration order. A plugin that
raises is recorded with its error, surfaced in **Settings → Plugins**, and never
crashes the host.

### What `plugin_api` gives you

`get_session` / `SessionLocal`, `get_settings`, the `Topic` model, `GitHubClient`
with its typed errors, the shared issue read models, and the
`require_github_repo` / `require_github_token` guards — so a plugin's GitHub
endpoints return exactly the same errors as core's. `PLUGIN_API_VERSION` is
bumped on any breaking change.

## MCP tools

```python
registry.add_mcp_server(name="board", title="Kanban boards", module="my_pkg.mcp_server")
```

The server is launched as `<running interpreter> -m my_pkg.mcp_server` with the
app's environment forwarded, so it reaches the same database, settings and
credentials as the UI — exactly how core's own in-tree servers work. Write it
with `FastMCP` and expose `main()`:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-plugin")


@mcp.tool()
async def list_things() -> list[dict]:
    """Docstrings are the tool description the model reads."""
    ...


def main() -> None:
    mcp.run()
```

It is registered as `<plugin_id>.<name>`, appears in **Settings → MCP servers**
attributed to the plugin, and inherits the per-surface enable toggles. Use
`url=` instead for a hosted `streamable_http` server.

## Frontend

A plugin's UI is a **separate bundle**, shipped inside the wheel and imported by
the SPA at runtime from `/api/plugins/<id>/assets/index.js`. Nothing has to be
built into core.

### Sections

```tsx
// web/src/index.tsx
import { SquareKanban } from "lucide-react";
import { registerSection } from "@precursor/host";

registerSection({
  id: "my-plugin",              // must match the backend descriptor's id
  label: "My section",
  icon: SquareKanban,
  description: "Shown on the home card.",
  openLabel: "Open it",
  colors: { /* Tailwind class tokens */ },
  accent: { light: "#0891b2", dark: "#22d3ee" },
  isEnabled: ({ settings }) => settings?.some_feature_enabled ?? false,
  Provider: ({ host, children }) => <MyProvider host={host}>{children}</MyProvider>,
  Sidebar: MySidebar,
  Main: MyMain,
  Title: MyTitle,
});
```

The `id` is also the section's top-level URL segment, so keep it URL-safe. The
section owns everything under that route: core hands it the remaining segments
and the hash and stays out of the way.

`isEnabled` is a *second* gate — the backend decides whether a section is
installed, this decides whether it currently applies (kanban needs a configured
GitHub repo). `Provider` wraps the app shell while the section is active, which
is where shared state goes: `Sidebar` and `Main` render into different subtrees.
Declaring `newLabel` adds a "New …" button to the sidebar header; omit it and
core hides the `+`.

### `SectionHost`

Every section component receives a `host` — the only thing core promises it:

| Field | Purpose |
| --- | --- |
| `segments` | Path segments after the section root. |
| `hash` | Current URL hash, without the `#`. |
| `navigate(segments, hash?, { push })` | Rewrite the section-relative URL (idempotent). |
| `openTopic(id)` | Leave the section and open a topic. |
| `settings` | App settings, or `null` while they load. |

### `@precursor/host`

The single module a plugin imports from. It re-exports the host's **own** React,
`react/jsx-runtime`, `react-dom` portals, the plugin registry, the HTTP client
(`api`, `request`, `apiErrorMessage`) and shared chrome (`Modal`, `Markdown`,
`EmptyHero`, `IssueLabelChip`, …). `HOST_API_VERSION` mirrors the backend's.

This matters for one hard reason: **there must be exactly one React on the
page.** A plugin bundling its own would give the app two dispatchers and every
hook the plugin called would throw. So plugin bundles mark these specifiers
*external*, and an import map injected into `index.html` points all of them at
the host's `host-runtime.js`.

### Building a plugin frontend

Mark the externals, emit one ES module named `index.js`, and write it into
`<import package>/web/`:

```ts
build: {
  outDir: "../src/my_pkg/web",
  lib: { entry: "src/index.tsx", formats: ["es"], fileName: () => "index.js" },
  rollupOptions: {
    external: [
      "react",
      "react-dom",
      "react/jsx-runtime",
      "react/jsx-dev-runtime",
      "@precursor/host",
    ],
  },
}
```

Match the host's React **major**; the instance you get at runtime is the host's.

**In-repo plugins** skip the separate npm project entirely and build with the
host's toolchain, which guarantees that match:

```bash
make plugins-build            # every plugin listed in PLUGINS
```

Their sources are type-checked by the host's `npm --prefix frontend run
typecheck`, via a `paths` mapping for `@precursor/host`.

### Bundling into core instead

`frontend/src/plugins/index.ts` still exists for a plugin that lives in this
repository and has no separate release cadence: drop a folder there whose entry
calls `registerSection` at module scope and import it. Whether it renders is
still the backend's call.

## Installing, enabling, removing

**Settings → Plugins** lists everything installed, what each contributes, and
its load error if it has one. From there you can install a package, toggle a
plugin, uninstall it, and restart.

Disabling is total, not cosmetic: the descriptors disappear (so the UI goes), the
routes answer 404, and the MCP servers leave the catalogue — applied immediately,
no restart.

Installing does need a restart, and the app offers a button for it. That is not
laziness: entry points are resolved once at startup and routers are mounted while
the app is built, so importing a new distribution into the live interpreter would
leave a half-installed plugin. Precursor runs the installer out-of-process, then
re-execs itself.

The install command is environment-specific — a `uv tool install` lives in an
isolated environment that `pip install` silently fails to extend — so the backend
detects which installer owns the instance and reports the one that works.

### Why installing is opt-in

Installing a package runs its build and import code with the privileges of
whoever runs Precursor, and Precursor has no authentication of its own. So the
*mutating* endpoints (install, uninstall, restart) sit behind three gates:

1. the app must be **bound to loopback**;
2. the request must **address** that loopback bind — the `Host` header is
   checked, and a cross-site `Origin` is rejected. A bind address alone is not a
   boundary: a page on an attacker's domain that DNS-rebinds to `127.0.0.1` is
   same-origin to the browser, so no CORS preflight ever runs;
3. the user must switch **Settings → Plugins → "Let Precursor install packages
   for me"** on. It is off by default.

Reading which plugins exist, and enabling or disabling them, is not gated — none
of that executes anyone's code. With the installer off, the panel still shows the
exact command to run by hand, which is the zero-risk path and always available.

## Stability

The contract may evolve before 1.0. We keep breakage to a minimum, bump
`PLUGIN_API_VERSION` / `HOST_API_VERSION`, and call it out in release notes.
