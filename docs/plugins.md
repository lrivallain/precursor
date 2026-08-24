# Plugins

Precursor is intentionally small. Anything that is not part of "topics, chat,
GitHub" should live in a plugin.

The **kanban board** is the reference implementation: it ships in this repo as a
separate distribution under `plugins/precursor-kanban/`, with a matching
frontend half in `frontend/src/plugins/kanban/`. Read those two folders
alongside this document.

## The two halves

A plugin has a Python half and a React half, joined by a shared **id**:

- The **backend** decides whether a contribution exists at all. It declares an
  entry point, and at startup Precursor calls its `register(registry)`.
- The **frontend** supplies the components. It registers itself against the same
  id in the SPA bundle.

Neither half renders without the other, which is what makes uninstalling a
plugin actually remove its UI: with the Python package gone no descriptor is
published, and the SPA never mounts the registration.

## Backend

Import from `precursor.plugin_api` — that module is the surface we keep stable,
unlike `precursor.backend.*`, which is internal and moves freely.

```toml
# pyproject.toml of the plugin package
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
```

`register` can:

| Call | Effect |
| --- | --- |
| `add_router(router)` | Mount a FastAPI `APIRouter`. Namespace it under `/api/<plugin>`. |
| `add_section(id=…, title=…, order=…)` | Contribute a whole application section (see below). The `id` is its route. |
| `add_frontend_extension(ext)` | Contribute a slot extension descriptor. |
| `add_mcp_tool(tool)` | Contribute an MCP tool. |

Discovery runs **once per process**, during `create_app()` — before the SPA
catch-all route, so plugin routes are reachable. Building a second app re-mounts
the already-loaded routers rather than re-running `register`. A plugin that
raises is logged and never crashes the host.

### What `plugin_api` gives you

Beyond the registry: `get_session` / `SessionLocal` (async DB), `get_settings`,
the `Topic` model, `GitHubClient` and its typed errors, the shared issue read
models, and the `require_github_repo` / `require_github_token` guards — so a
plugin's GitHub endpoints return exactly the same errors as core's.
`PLUGIN_API_VERSION` is bumped on any breaking change.

## Frontend

### Sections

A **section** is a top-level surface: a sidebar rail entry, a home-screen card, a
command-palette entry and a route at `/<id>` — the same `id` both halves are
keyed by, so keep it URL-safe. The section owns everything under its root: core
treats the remaining URL segments and the hash as opaque and hands them over.

```tsx
// frontend/src/plugins/my-plugin/index.tsx
import { SquareKanban } from "lucide-react";
import { registerSection } from "../../lib/plugins";

registerSection({
  id: "my-plugin",              // must match the backend descriptor's id
  label: "My section",
  icon: SquareKanban,
  description: "Shown on the home card.",
  openLabel: "Open it",
  colors: { /* Tailwind class tokens — see lib/sections.ts */ },
  accent: { light: "#0891b2", dark: "#22d3ee" },
  isEnabled: ({ settings }) => settings?.some_feature_enabled ?? false,
  Provider: ({ host, children }) => <MyProvider host={host}>{children}</MyProvider>,
  Sidebar: MySidebar,
  Main: MyMain,
  Title: MyTitle,
});
```

Then import it for its side effects from `frontend/src/plugins/index.ts`.

`isEnabled` is the *second* gate: the backend decides whether the section is
installed, this decides whether it currently applies (kanban, for instance,
needs a configured GitHub repo). A section that becomes disabled while open
falls back to Topics.

`Provider` is optional and wraps the whole app shell while the section is
active — the `Sidebar` and `Main` panes render into different subtrees, so
shared state belongs in a context mounted there.

Declare `newLabel` to get a "New …" affordance in the sidebar header; omit it and
core hides the `+` entirely.

### `SectionHost`

Every section component receives a `host`, the only thing core promises it:

| Field | Purpose |
| --- | --- |
| `segments` | Path segments after the section root. |
| `hash` | Current URL hash, without the `#`. |
| `navigate(segments, hash?, { push })` | Rewrite the section-relative URL. |
| `openTopic(id)` | Leave the section and open a topic. |
| `settings` | App settings, or `null` while they load. |

Call `navigate` freely — it is idempotent, so re-asserting the current URL costs
nothing.

### Colours

A section ships its own `colors` (Tailwind class strings, mirroring
`lib/sections.ts`) and an `accent` pair. The accent is injected as a real
stylesheet rule at registration, because `--section-accent` has to resolve
differently under `.dark` and Tailwind only emits classes it can find in source.

### Slot extensions

For narrower contributions, register a renderer for a descriptor `kind`:

```ts
import { registerRenderer } from "../../lib/plugins";

registerRenderer("panel", MyPanel);
```

`MyPanel` receives the `descriptor` (id, slot, title, config) and is mounted
wherever the SPA renders that slot.

> **Note** — sections are wired end-to-end and exercised by the kanban plugin.
> The other kinds below are designed, but their mount points are not all live.

| Kind | Slot examples | Use case |
| --- | --- | --- |
| `panel` | `topic.sidebar.bottom` | Side-by-side context (e.g. PR diff) |
| `message-renderer` | `chat.message.body` | Mermaid / drawio / chart blocks |
| `settings-tab` | `settings.tabs` | Per-plugin configuration UI |
| `topic-action` | `topic.header.actions` | Buttons that operate on the topic |

## Packaging

The SPA is pre-built and served by FastAPI, so a plugin's frontend must be part
of that bundle today: in-repo plugins live under `frontend/src/plugins/`, and the
backend descriptor still decides whether they appear. Loading a frontend half
from an out-of-tree package is not supported yet.

An in-repo plugin is a `uv` workspace member:

```toml
# root pyproject.toml
[tool.uv.workspace]
members = ["plugins/*"]

[tool.uv.sources]
precursor-kanban = { workspace = true }
```

Ship it to users as an optional extra (`precursor-ai[kanban]`) so the default
install keeps working either way, and add its test directory to `testpaths` so
`make test` covers it.

## Stability

The contract may evolve before 1.0; we'll keep breakage to a minimum, bump
`PLUGIN_API_VERSION`, and call it out in release notes.
