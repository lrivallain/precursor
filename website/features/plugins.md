---
title: Plugins
---

# Plugins

Precursor is intentionally small. Anything that isn't part of "topics, chat,
GitHub" is meant to live in a **plugin** — and a plugin is just one Python
package you install.

The [Kanban board](/features/kanban) is the first official one and the reference
implementation: it lives in the repository as its own distribution
(`precursor-kanban`) with its own routes, schemas, MCP tools, frontend and
release cadence.

## What a plugin can bring

| | |
| --- | --- |
| 🧩 **A whole section** | Its own entry in the sidebar rail, a card on the home launcher, a ⌘K entry and a top-level route — with UI it ships itself. |
| 🔌 **MCP tools** | Its own tool server, joining the same catalogue and toggles as the built-ins, so the assistant can use it. |
| ⚙️ **Its own settings page** | An entry in the Settings modal under a **Plugins** group, backed by a namespaced store core never reads. |
| 🛠 **API routes** | FastAPI endpoints mounted under `/api/<plugin>`. |

Install the package, restart, and all three appear. Uninstall it and they all go
— there is no leftover half-feature in the sidebar.

## Installing one

**Settings → Plugins** lists everything installed, what each contributes, and any
load error. You can install a package, toggle it, uninstall it, and restart from
there.

<Screenshot src="/screenshots/plugins.png" alt="The Plugins settings panel listing precursor-kanban with its sections, API routes and MCP servers" caption="Settings → Plugins: each installed package with the sections, routes and MCP servers it contributes." />

Or from a terminal, into the same environment as Precursor:

```bash
uv pip install precursor-kanban
```

The panel shows the command that works for *your* install — a
`uv tool install` lives in an isolated environment that `pip install` silently
fails to extend, so Precursor detects which installer owns the instance rather
than guessing.

::: tip Why the restart
Entry points are resolved once at startup and routes are mounted while the app is
built, so a package imported into the live process would be only half installed.
Precursor runs the installer out-of-process and offers a **Restart now** button.
Disabling, by contrast, is instant.
:::

::: warning Installing is opt-in
Installing a package runs its code with Precursor's privileges, and the app has
no authentication of its own — so the in-app installer is **off by default**, only
answers requests addressed to Precursor's own localhost address, and has to be
switched on in the panel. Listing and toggling plugins is always available; so is
running the command yourself.
:::

## How the UI works

This is the part that makes a plugin feel native rather than bolted on. A
plugin's frontend is a **separate bundle shipped inside its Python wheel**, which
Precursor serves and the app imports at runtime.

The catch is that there must be exactly one React on the page — a second copy
would break every hook a plugin calls. So plugin bundles leave `react`,
`react-dom`, `react/jsx-runtime` and `@precursor/host` **external**, and an
import map points all of them at the host's own runtime module. A plugin gets the
app's React *and* its SDK — the HTTP client, shared components, the section
registry — without vendoring any of it.

## Writing one

```python
# my_pkg/plugin.py
from precursor.plugin_api import PluginRegistry

def register(registry: PluginRegistry) -> None:
    registry.add_router(router)
    registry.add_section(id="my-plugin", title="My section")
    registry.add_settings_page(title="My plugin")
    registry.add_mcp_server(name="tools", module="my_pkg.mcp_server")
```

```tsx
// web/src/index.tsx
import { registerSection } from "@precursor/host";

registerSection({ id: "my-plugin", label: "My section", Sidebar, Main, /* … */ });
```

Import from **`precursor.plugin_api`**, not `precursor.backend.*`: it's the
surface we keep stable, and it hands you async database sessions, settings, the
GitHub client and its shared guards so a plugin behaves exactly like core does.

The full contract — `SectionHost`, the SDK's exports, the build settings, the
MCP server shape — is in the [plugin reference](/reference/plugins).

::: info Stability
Sections, MCP servers and runtime-loaded UI are wired end-to-end and exercised by
the kanban plugin. Narrower extension kinds (`panel`, `message-renderer`,
`settings-tab`, `topic-action`) are designed but their mount points are still
being wired — see the [reference](/reference/plugins).
:::
