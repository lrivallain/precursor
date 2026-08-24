---
title: Plugin reference
---

# Plugin reference

This is the detailed contract for extending Precursor. For a friendly overview,
see the [Plugins feature guide](/features/plugins).

The [Kanban board](/features/kanban) is the reference implementation: it lives in
the repository as a separate distribution under `plugins/precursor-kanban/`, with
its frontend half in `frontend/src/plugins/kanban/`.

## Design principle

Precursor is intentionally small. Anything that is **not** part of "topics, chat,
GitHub" should live in a plugin rather than growing core.

## The two halves

A plugin has a Python half and a React half, joined by a shared **id**:

- The **backend** decides whether a contribution exists at all — it declares an
  entry point and Precursor calls its `register(registry)` at startup.
- The **frontend** supplies the components, registered against the same id.

Neither renders without the other, which is what makes uninstalling a plugin
actually remove its UI: with the Python package gone no descriptor is published,
and the SPA never mounts the registration.

## Backend contract

A plugin is a Python package that exposes a `register(registry)` callable and
declares it as an entry point in the `precursor.plugins` group:

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

Import from **`precursor.plugin_api`**, not `precursor.backend.*`: the former is
the surface we keep stable, the latter is internal and moves freely.

### What the registry accepts

| Call | Effect |
| --- | --- |
| `add_router(router)` | Mount a FastAPI `APIRouter`. Namespace it under `/api/<plugin>`. |
| `add_section(id=…, title=…, order=…)` | Contribute a whole application section. The `id` is its route. |
| `add_frontend_extension(ext)` | Contribute a slot extension descriptor. |
| `add_mcp_tool(tool)` | Contribute an [MCP](/features/mcp) tool. |

Discovery runs **once per process**, during `create_app()` — deliberately before
the SPA catch-all route, since routes match in registration order and a plugin
router mounted after it would be shadowed. Building a second app re-mounts the
already-loaded routers rather than re-running `register`. A plugin that raises is
**logged and never crashes the host**.

### What `plugin_api` exports

Beyond the registry: `get_session` / `SessionLocal` (async database access),
`get_settings`, the `Topic` model, `GitHubClient` with its typed errors, the
shared issue read models (`IssueDetail`, `IssueComment`, `IssueLabel`), and the
`require_github_repo` / `require_github_token` guards — so a plugin's GitHub
endpoints return exactly the same status codes and messages as core's.

`PLUGIN_API_VERSION` is bumped on any breaking change.

## Frontend contract

### Sections

A **section** is a top-level surface: an entry in the sidebar rail, a card on the
home launcher, an entry in the command palette (⌘K) and a route at `/<id>` — the
same `id` both halves are keyed by, so keep it URL-safe. The section owns
everything under its root: core treats the remaining URL segments and the hash as
opaque and hands them over.

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

| Field | Purpose |
| --- | --- |
| `id` | Must match the backend descriptor's `id`; also the URL segment. |
| `label`, `icon` | Sidebar rail + home card identity. |
| `description`, `openLabel` | Home card copy. |
| `keywords` | Extra command-palette search terms. |
| `colors`, `accent` | Section palette (see below). |
| `newLabel` | Enables the sidebar "New …" action; omit for no create flow. |
| `isEnabled` | Second gate — is the section *currently* applicable? |
| `Provider` | Optional wrapper around the app shell while active. |
| `Sidebar`, `Main`, `Title` | The rendered components. |

`isEnabled` is distinct from installation: the backend decides whether the
section exists, this decides whether it applies right now (the kanban board, for
instance, needs a configured GitHub repo). A section that becomes disabled while
open falls back to Topics.

`Provider` matters because `Sidebar` and `Main` render into different subtrees —
state they share belongs in a context mounted there.

### `SectionHost`

Every section component receives a `host`, the only thing core promises it:

| Field | Purpose |
| --- | --- |
| `segments` | Path segments after the section root (`/kanban/4-board` → `["4-board"]`). |
| `hash` | Current URL hash, without the `#`. |
| `navigate(segments, hash?, { push })` | Rewrite the section-relative URL. |
| `openTopic(id)` | Leave the section and open a topic. |
| `settings` | App settings, or `null` while they load. |

`navigate` is idempotent, so re-asserting the current URL is free.

### Colours

A section ships its own `colors` (Tailwind class strings mirroring
`lib/sections.ts`) and an `accent` pair. The accent is injected as a real
stylesheet rule at registration, because `--section-accent` must resolve
differently under `.dark` — which an inline style can't express — and Tailwind
only emits class names it can find in source.

### Slot extensions

For narrower contributions, register a renderer for a descriptor `kind`:

```ts
import { registerRenderer } from "../../lib/plugins";

registerRenderer("panel", MyPanel);
```

`MyPanel` receives the `descriptor` (`id`, `slot`, `title`, `config`) and is
mounted wherever the SPA renders that slot.

## `FrontendExtension` descriptor

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | string | Stable unique id; the SPA looks its registration up by this. |
| `kind` | string | Which renderer handles it. |
| `slot` | string | Where in the SPA it mounts. |
| `title` | string | Human-readable label. |
| `config` | object | Free-form payload. Sections use `order`. |

## Plugin kinds

::: warning
`section` is wired end-to-end and exercised by the kanban plugin. The kinds below
it are designed, but not every mount point is live in core yet.
:::

| Kind | Slot | Use case |
| --- | --- | --- |
| `section` | `app.section` | A whole surface: sidebar, route, home card |
| `panel` | `topic.sidebar.bottom` | Side-by-side context (e.g. a PR diff) |
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

Ship it as an optional extra (`precursor-ai[kanban]`) so the default install
keeps working either way, and add its test directory to `testpaths` so
`make test` covers it.

## MCP tools from a plugin

Because Precursor is an [MCP client](/features/mcp), a plugin can also register
external MCP tool servers, contributing new tools the assistant can call without
touching core.

## Stability

The contract may evolve before 1.0. We keep breakage to a minimum, bump
`PLUGIN_API_VERSION`, and call it out in the release notes.
