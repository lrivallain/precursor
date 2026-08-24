---
title: Plugins
---

# Plugins

Precursor is intentionally small. Anything that isn't part of "topics, chat,
GitHub" is meant to live in a **plugin**. Plugins extend both halves of the app —
the FastAPI backend and the React SPA — without forking core.

The [Kanban board](/features/kanban) is the first official plugin and the
reference implementation: it lives in the repository as its own distribution
(`plugins/precursor-kanban/`) with its own routes, schemas, tests and release
cadence, and installs as `precursor-ai[kanban]`. Remove the package and the
section disappears from the app.

This page is a feature overview; for the full contract and API, see the
[plugin reference](/reference/plugins).

## Two halves, one id

A plugin has a Python half and a React half, joined by a shared **id**. The
backend decides whether a contribution exists at all; the frontend supplies the
components. Neither renders without the other — which is exactly why
uninstalling a plugin removes its UI.

## Backend plugins

A backend plugin is a Python package that exposes a `register(registry)` callable
and declares it as an entry point:

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

Import from **`precursor.plugin_api`** — a curated, stable surface that hands you
async database sessions, settings, the GitHub client and its shared repo/token
guards, so a plugin behaves exactly like core does.

Plugins are discovered once at startup; a failing plugin is **logged and never
crashes the host**.

## Sections

The headline capability: a plugin can own a **whole application section** — an
entry in the sidebar rail, a card on the home launcher, an entry in the command
palette (⌘K) and a top-level route.

```tsx
registerSection({
  id: "my-plugin",
  label: "My section",
  icon: SquareKanban,
  isEnabled: ({ settings }) => settings?.some_feature_enabled ?? false,
  Sidebar: MySidebar,
  Main: MyMain,
});
```

The section owns everything under its route (`/<id>`): core hands it the
remaining URL segments and the hash and stays out of the way. It also gets a second gate —
`isEnabled` — for capabilities that depend on configuration rather than
installation, the way the kanban board needs a GitHub repo.

## Frontend extensions

For narrower contributions, the SPA fetches `/api/plugins` on boot and matches
each descriptor's `kind` to a registered renderer:

```ts
import { registerRenderer } from "../../lib/plugins";

registerRenderer("panel", MyPanel);
```

| Kind | Slot | Use case |
| --- | --- | --- |
| `section` | `app.section` | A whole surface: sidebar, route, home card |
| `panel` | `topic.sidebar.bottom` | Side-by-side context (e.g. a PR diff) |
| `message-renderer` | `chat.message.body` | Mermaid / drawio / chart blocks |
| `settings-tab` | `settings.tabs` | Per-plugin configuration UI |
| `topic-action` | `topic.header.actions` | Buttons that operate on the topic |

::: info Stability
`section` is wired end-to-end and exercised by the kanban plugin. The remaining
kinds are designed, and their mount points are being wired progressively — see
the [plugin reference](/reference/plugins) for what's live today.

One known limit: the SPA is pre-built and served by FastAPI, so a plugin's
frontend has to ship in that bundle. Out-of-tree frontend halves aren't
supported yet.
:::
