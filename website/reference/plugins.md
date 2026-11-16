---
title: Plugin reference
---

# Plugin reference

The contract for extending Precursor. For installing and managing plugins, see
the [Plugins guide](/features/plugins). The worked example is
[`precursor-kanban`](https://github.com/lrivallain/precursor-kanban).

A plugin is **one Python package** that can contribute:

| Contribution | Registered with | Result |
| --- | --- | --- |
| **API routes** | `registry.add_router(...)` | FastAPI routes, conventionally under `/api/<plugin>` |
| **A section** | `registry.add_section(...)` + a built ES module in the wheel | sidebar rail entry, home card, ⌘K entry, route at `/<id>` |
| **A settings page** | `registry.add_settings_page(...)` | its own page in Settings, with a namespaced store |
| **MCP tools** | `registry.add_mcp_server(...)` | a server in the MCP catalogue, with core's toggles |

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

Import only from **`precursor.plugin_api`**, the stable surface;
`precursor.backend.*` is internal.

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
    registry.add_section(id="my-plugin", title="My section")  # order=100 by default; core uses 0-99
    registry.add_settings_page(title="My plugin")
    registry.add_mcp_server(name="tools", module="my_pkg.mcp_server")
```

- The entry-point name is the **plugin id** and the namespace for everything it
  registers. Calls outside `register()` raise.
- Discovery runs **once per process** in `create_app()`, before the SPA
  catch-all. A plugin that raises is recorded with its error, shown in
  **Settings → Plugins**, and never crashes the host.

### What `plugin_api` gives you

| Export | Use |
| --- | --- |
| `PluginRegistry`, `FrontendExtension` | registration |
| `get_session`, `SessionLocal` | DB session in a route / outside a request |
| `get_settings`, `Settings` | process settings |
| `get_plugin_settings`, `read_plugin_settings` | the plugin's own [settings](#settings-pages) |
| `Topic` | the topic model |
| `GitHubClient`, `GitHubInsufficientScopeError`, `GitHubRepoNotAccessibleError`, `IssueDetail`, `IssueComment`, `IssueLabel` | GitHub access and shared read models |
| `require_github_repo`, `require_github_token`, `resolve_github_token`, `resolve_global_github_repo`, `resolve_issue_associations_enabled` | the same guards and resolvers core uses, so errors match |
| `PLUGIN_API_VERSION` | currently **1**; bumped on breaking changes |

`precursor` ships `py.typed`, so `plugin_api` type-checks against the installed
wheel.

## MCP tools

```python
registry.add_mcp_server(name="board", title="Kanban boards", module="my_pkg.mcp_server")
```

The server runs as `<running interpreter> -m my_pkg.mcp_server` with the app's
environment forwarded (same DB, settings, credentials). Use `url=` for a hosted
`streamable_http` server instead. Write it with the MCP SDK's `MCPServer` and
expose `main()`:

```python
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("my-plugin")


@mcp.tool()
async def list_things() -> list[dict]:
    """Docstrings are the tool description the model reads."""
    ...


def main() -> None:
    mcp.run()
```

::: warning Target MCP 2
The server runs in Precursor's environment, so it gets Precursor's SDK:
**`mcp>=2.2,<3`**. MCP 2 renamed `FastMCP` to `MCPServer`
(`mcp.server.fastmcp` no longer imports), so MCP 1 plugins fail at startup.
Declare `mcp>=2.2,<3` yourself. See the
[MCP v2 migration guide](https://py.sdk.modelcontextprotocol.io/v2/migration/).
:::

The server is registered as `<plugin_id>.<name>` and appears in **Settings → MCP
servers** attributed to the plugin. Keep server and tool names as they are: core
generates provider-safe aliases for the model and routes calls back.

## Frontend

The UI is a **separate ES module shipped in the wheel**, imported at runtime
from `/api/plugins/<id>/assets/index.js`.

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
  Provider: ({ host, children }) => <MyProvider host={host}>{children}</MyProvider>,
  Sidebar: MySidebar,
  Main: MyMain,
  Title: MyTitle,
  newLabel: "Add a project",                      // optional "New …" button…
  onNew: (host) => host.openSettings("my-plugin"), // …shown only with onNew
});
```

- `id` is the top-level URL segment (keep it URL-safe); the section owns every
  path under it.
- A registered section **always appears**. If it needs setup, say so in `Main`
  rather than hiding.
- `Provider` wraps the shell while the section is active: put shared state
  there, since `Sidebar` and `Main` render in different subtrees.
- A registration without a matching backend descriptor renders nothing.

### Settings pages

```python
registry.add_settings_page(title="My plugin")  # id defaults to the plugin id
```

```tsx
import { registerSettingsPage, usePluginSettings } from "@precursor/host";

function MyPanel() {
  const { value, setValue, save, saving, dirty } = usePluginSettings("my-plugin", {
    some_option: "",
  });
  if (value === null) return null;
  return /* … your form … */;
}

registerSettingsPage({ id: "my-plugin", label: "My plugin", icon: Cog, Component: MyPanel });
```

Values are **one opaque JSON document per plugin** at
`/api/plugins/installed/<id>/settings` (`plugin.<id>` in the settings table).
`PUT` replaces the whole document. The backend reads the same document:

```python
from precursor.plugin_api import get_plugin_settings, read_plugin_settings

values = await get_plugin_settings("my-plugin")  # own session; works in the MCP subprocess
values = await read_plugin_settings(session, "my-plugin")  # inside a request
```

::: warning Don't put secrets in there
The document is returned to the client verbatim: core can't know which key is a
credential, so nothing is redacted.
:::

### `SectionHost`

Every section component receives `host`:

| Field | Purpose |
| --- | --- |
| `segments` | path segments after the section root |
| `hash` | URL hash without `#` |
| `navigate(segments, hash?, { push })` | rewrite the section-relative URL (idempotent) |
| `openTopic(id)` | leave the section and open a topic |
| `openSettings(pageId?)` | open Settings, optionally on a plugin page |
| `setPageTitle(title)` | browser tab title (`<title> · <label> · Precursor`); `null` resets. Newer than `HOST_API_VERSION` 2: call as `host.setPageTitle?.(…)` |
| `settings` | app settings, `null` while loading |

### `@precursor/host`

The one module a plugin imports from. It re-exports the host's **own** React,
`react/jsx-runtime`, `react-dom` portals, the registry, the HTTP client (`api`,
`request`, `apiErrorMessage`) and shared UI (`Modal`, `Markdown`, `EmptyHero`,
`IssueLabelChip`, `ContextMenu`, `useConfirm`, …). `HOST_API_VERSION` is
currently **2** and moves independently of `PLUGIN_API_VERSION`.

There must be **exactly one React on the page**, so bundles mark these
specifiers external and an import map in `index.html` points them at the host's
`host-runtime.js`.

### Building a plugin frontend

Emit one ES module, `index.js`, into `<import package>/web/`:

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

Match the host's React **major**.

::: warning `web/` is a build product
Generate it into the Python package so it ships in the wheel; don't commit it.
Without it the section is advertised but nothing loads (Settings → Plugins
flags this).
:::

Two things fail silently outside a host repository:

**Declare `@precursor/host` yourself.** Nothing is installed for it (the import
map supplies it), so write an ambient declaration for what you import:

```ts
// types/precursor-host.d.ts
declare module "@precursor/host" {
  import type { ComponentType, ReactNode } from "react";
  export const HOST_API_VERSION: number;
  export function registerSection(section: SectionPlugin): void;
  export function request<T>(path: string, init?: RequestInit): Promise<T>;
  // …only what you actually import.
}
```

**Ship your own Tailwind utilities.** The host never scans your compiled
sources, so build the utilities into your bundle, mapped onto the host's theme
variables:

```css
/* styles.css — no preflight: the host has already applied its own. */
@layer theme, base, components, utilities;
@import "tailwindcss/theme.css" layer(theme);
@import "tailwindcss/utilities.css" layer(utilities);
@source "./src";
@custom-variant dark (&:where(.dark, .dark *));
@theme {
  --color-bg: var(--bg);
  --color-surface: var(--surface);
  --color-border: var(--border);
  --color-text: var(--text);
  --color-muted: var(--muted);
  --color-accent: var(--accent);
}
```

```ts
// Injected once, at module scope, before the section registers.
import css from "./styles.css?inline";
const style = document.createElement("style");
style.textContent = css;
document.head.prepend(style);   // prepend: the host still wins on collisions
```

### Bundling into core instead

For a plugin living in this repository with no separate release:
add a folder under `frontend/src/plugins/` whose entry calls `registerSection`
at module scope, and import it from `frontend/src/plugins/index.ts`. The backend
descriptor still decides whether it renders.

## Installing, enabling, removing

User-facing behaviour is in the [Plugins guide](/features/plugins#installing-one).
Mechanics:

- **Disable** removes descriptors (UI), makes routes answer `404` and drops MCP
  servers, live.
- **Install** runs the installer out-of-process, then re-execs Precursor: entry
  points and routers are bound at startup.
- The backend detects which installer owns the instance (`uv tool`, `uv pip`,
  `pip`) and reports the command that works.

### How an install persists

A `uv tool` environment is rebuilt from the requirements in its
`uv-receipt.toml`. Precursor reads the receipt before every rebuild and restates
the host's extras, its pinned wheel and every plugin installed alongside, so a
plugin needs **no entry in core's metadata** (the `kanban` extra is only a
convenience).

- `precursor service update` carries plugins across. A same-commit plugin wheel
  published with a release wins over the plain name.
- If a plugin can't be served, the update retries without it and says so.
- Adding or removing a plugin on a **nightly** host re-points the host at the
  current nightly wheel (the old one is deleted when the release is replaced);
  the response's `host_upgrade` reports it.
- Upgrading one plugin uses `--upgrade-package <plugin>` and restates the rest.

With `uv pip` / `pip`, Precursor's own requirements (without its extras) are
passed as a **constraints file**, so a plugin can't downgrade core
dependencies. Removing a distribution that wasn't installed alongside the tool
(a dependency of Precursor or an extra) is refused: disable it instead.

### Why installing is opt-in

Installing runs third-party code with Precursor's privileges and Precursor has
no authentication. Install, upgrade, uninstall and restart require all three:

1. the app is **bound to loopback**;
2. the request **addresses** that bind: the `Host` header is checked and a
   cross-site `Origin` rejected (a bind address alone doesn't stop DNS
   rebinding);
3. **Settings → Plugins → "Let Precursor install packages for me"** is on (off
   by default).

Listing, enabling and disabling are not gated. With the installer off, the
panel shows the exact command to run.

## The catalogue

`GET /api/plugins/catalog` serves the **Available** list from the bundled
directory `website/plugins/` (shipped in the wheel as `precursor/catalog`; a
checkout reads `website/plugins`). One markdown file per plugin: frontmatter is
the metadata, the body is the page at `/plugins/<id>`. Only files whose
frontmatter has `distribution` are entries.

```yaml
---
title: My plugin              # required
description: Does a thing.    # required, one line
plugin: my-plugin             # entry-point name; must equal the file name
distribution: precursor-my-plugin  # PyPI project name (required, bare)
homepage: https://github.com/you/precursor-my-plugin
repository: https://github.com/you/precursor-my-plugin  # exactly https://github.com/<owner>/<repo>
author: you
license: MIT
tags: [github, notes]
contributes: [section, settings, mcp, api]
recommended: false            # maintainers set this
---
```

`distribution` is the only value handed to an installer, so it must match PEP
508's **bare name** grammar (no URL, path, `@`, extra or version). This is
checked at load time (an invalid entry is logged and skipped) and in
`tests/test_plugin_catalog.py`. Installing from the catalogue calls the same
gated endpoint as typing a name.

To get listed, see [Submitting a plugin](/plugins/submitting).

## Versioning a plugin

Version independently of the host, from your own git tags:

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]

[project]
dynamic = ["version"]

[tool.hatch.version]
source = "vcs"
```

Avoid a static version: identical wheel filenames make uv skip downloads from
URL-based channels (like the nightly manifest), and PyPI rejects re-uploads.

State the minimum host version in your README. **Don't depend on
`precursor-ai`** in `[project] dependencies`: the host imports you, and
`precursor-ai[<your-extra>]` would form a cycle.

## Stability

The contract may change before 1.0. Breaking changes bump `PLUGIN_API_VERSION`
or `HOST_API_VERSION` and are called out in release notes. Only `section` and
`settings-page` descriptors have mount points today; generic slot extensions
(`add_frontend_extension` + `registerRenderer`) are defined but not yet rendered
anywhere.
