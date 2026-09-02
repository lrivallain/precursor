---
title: Plugin reference
---

# Plugin reference

The full contract for extending Precursor. For a friendly overview, see the
[Plugins feature guide](/features/plugins).

Precursor is intentionally small. Anything that is not part of "topics, chat,
GitHub" should live in a plugin.

A plugin is **one Python package** that can bring three things:

| Contribution | How | Where it shows up |
| --- | --- | --- |
| **Backend** | `registry.add_router(...)` | FastAPI routes under `/api/<plugin>` |
| **UI** | `registry.add_section(...)` + a built ES module in the wheel | A whole section: sidebar rail, home card, ⌘K entry, route at `/<id>` |
| **Settings** | `registry.add_settings_page(...)` | Its own page in the Settings modal, with a namespaced store |
| **Tools** | `registry.add_mcp_server(...)` | The MCP catalogue, with the same toggles as core's built-ins |

Install the package, restart, and all three appear. Uninstall it and they all
go. The [kanban board](/features/kanban) is the reference implementation — it
ships from its own repository,
[`precursor-kanban`](https://github.com/lrivallain/precursor-kanban).

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
    registry.add_settings_page(title="My plugin")
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
  Provider: ({ host, children }) => <MyProvider host={host}>{children}</MyProvider>,
  Sidebar: MySidebar,
  Main: MyMain,
  Title: MyTitle,
});
```

The `id` is also the section's top-level URL segment, so keep it URL-safe. The
section owns everything under that route: core hands it the remaining segments
and the hash and stays out of the way.

A registered section always appears. Sections could once gate themselves on app
state and hide until it was satisfied, which made an installed, enabled plugin
silently absent — indistinguishable from a broken one. If your section needs
setup (kanban wants a configured GitHub repository), say so from `Main`, where
the user actually lands, rather than removing the section from the sidebar.

`Provider` wraps the app shell while the section is active, which is where shared
state goes: `Sidebar` and `Main` render into different subtrees.

`onNew` puts a "New …" button in the sidebar header, labelled by `newLabel` and
tinted with the section's own palette — core owns the button so every section's
sits in the same place, the section owns what it does. Omit `onNew` and there is
no button; a label with nothing behind it is worse than none. The kanban board
uses it to open its own settings, since "new" there means "track another
project":

```tsx
newLabel: "Add a project",
onNew: (host) => host.openSettings("kanban"),
```

### Settings pages

A plugin with configuration declares a page and gets its own entry in the
Settings modal, under a **Plugins** group:

```python
registry.add_settings_page(title="My plugin")   # id defaults to the plugin's
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

Values are stored as **one opaque JSON object per plugin**, at
`/api/plugins/installed/<id>/settings` (and `plugin.<id>` in the settings table).
Core never looks inside it, which is the point: a plugin can add, rename and drop
its own keys without touching core's settings schema, and two plugins can't
collide. `PUT` replaces the whole document, so a plugin can remove a key it no
longer uses.

The plugin's **backend** reads the same blob:

```python
from precursor.plugin_api import get_plugin_settings, read_plugin_settings

values = await get_plugin_settings("my-plugin")          # opens its own session
values = await read_plugin_settings(session, "my-plugin")  # inside a request
```

`get_plugin_settings` works from anywhere — including the plugin's MCP
subprocess — so a tool server can read what the panel wrote.
::: warning Don't put secrets in there
Unlike core's own settings, a plugin's blob is returned to the client verbatim —
there is no redaction, because core has no schema telling it which key is a
credential. A plugin needing a token should keep it out of this store.
:::


### `SectionHost`

Every section component receives a `host` — the only thing core promises it:

| Field | Purpose |
| --- | --- |
| `segments` | Path segments after the section root. |
| `hash` | Current URL hash, without the `#`. |
| `navigate(segments, hash?, { push })` | Rewrite the section-relative URL (idempotent). |
| `openTopic(id)` | Leave the section and open a topic. |
| `openSettings(pageId?)` | Open the Settings modal, on a plugin's own page when named. |
| `settings` | App settings, or `null` while they load. |

### `@precursor/host`

The single module a plugin imports from. It re-exports the host's **own** React,
`react/jsx-runtime`, `react-dom` portals, the plugin registry, the HTTP client
(`api`, `request`, `apiErrorMessage`) and shared chrome (`Modal`, `Markdown`,
`EmptyHero`, `IssueLabelChip`, `ContextMenu`, `useConfirm`, …).
`HOST_API_VERSION` is this contract's version (currently **2**); the backend's
`PLUGIN_API_VERSION` is the parallel number for the Python contract and moves
independently.

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
::: warning A plugin's frontend is a build product
`<package>/web/` is generated, not committed — your build writes it into the
Python package so it ships in the wheel. Without it the backend still advertises
the section, the SPA has nothing to import, and the section silently doesn't
appear — Settings → Plugins says so when it detects that state.
:::


#### Two things a host repository would have done for you

Nothing is installed for `@precursor/host`, and nothing scans your sources for
Tailwind classes. Both failures are silent.

**Declare `@precursor/host` yourself.** Nothing is ever installed for that
specifier — the import map supplies it at runtime — so TypeScript has nothing to
resolve. Write an ambient declaration for the members you import:

```ts
// types/precursor-host.d.ts
declare module "@precursor/host" {
  import type { ComponentType, ReactNode } from "react";
  export const HOST_API_VERSION: number;
  export function registerSection(section: SectionPlugin): void;
  export function request<T>(path: string, init?: RequestInit): Promise<T>;
  // …only what you actually import: a narrow shim is one you can keep true.
}
```

The Python side needs no equivalent: `precursor` ships a `py.typed` marker, so
`precursor.plugin_api` type-checks against the installed wheel.

**Ship your own Tailwind utilities.** Tailwind scans the sources it is pointed
at, and yours ship compiled — so nothing generates your classes and the result
is a plugin that renders correct markup with no styling, only in a real install.
Build the utilities into your bundle and inject them, mapping the theme tokens
onto the host's variables so your UI still follows the app's theme and dark
mode:

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

[`precursor-kanban`](https://github.com/lrivallain/precursor-kanban) is the
worked example of both.

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
routes answer 404, and the MCP servers leave the catalogue. It applies live — the
sidebar, home launcher and command palette update while the panel stays open.

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

## The catalogue

The **Available** list in Settings → Plugins is the catalogue: a curated
directory of installable plugins, served from `GET /api/plugins/catalog`.

It is **bundled with Precursor, not fetched**. That works offline, adds no
network failure states, phones nothing home, and means every entry was reviewed
in a pull request before it shipped — at the cost of a newly listed plugin
arriving with the next release. For a catalogue of a handful of curated entries
that is the right side of the trade.

### One file is one plugin

Each entry is a markdown page under `website/plugins/`: its **YAML frontmatter is
the metadata**, its **body is the documentation** published at `/plugins/<id>`
and bundled into the app's own `/docs`. A page counts as an entry if — and only
if — its frontmatter carries `distribution`, so the catalogue's own index and
submission guide sit in the same folder as ordinary pages.

```yaml
---
title: My plugin              # display name (required)
description: Does a thing.    # one-line summary (required)
plugin: my-plugin             # entry-point name; must equal the file name
distribution: precursor-my-plugin  # PyPI project name (required)
homepage: https://github.com/you/precursor-my-plugin
author: you
license: MIT
tags: [github, notes]
contributes: [section, settings, mcp, api]
recommended: false            # maintainers set this
---
```

The build hook relocates that directory to `precursor/catalog` inside the wheel;
a source checkout falls back to `website/plugins`, exactly as the SPA and docs
bundles do.

### `distribution` is a bare name, and it is enforced

It is the only value ever handed to an installer, so it is validated against PEP
508's name grammar: no URL, no path, no `@ …` requirement, no extra, no version
specifier. A catalogue able to say `pkg @ https://example.invalid/evil.whl` would
turn a merged pull request into code execution on every machine that opened the
panel.

The check runs at load time *and* in `tests/test_plugin_catalog.py`, so a
malformed entry fails CI rather than shipping. At runtime an invalid entry is
logged and skipped — one bad file must never take the panel down.

Installing from the catalogue calls the same gated endpoint as typing a package
name by hand. The catalogue is a shortcut to a name, not a second way in.

### Getting listed

Add the file, open a pull request. The full checklist is in
[Submitting a plugin](/plugins/submitting).

## Versioning a plugin

A plugin versions **independently of the host**. Precursor is CalVer
(`YYYY.M.MICRO`) from its own git tags, and a plugin is free to do the same from
its own — releasing when it has something to release, not when core does:

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]

[project]
dynamic = ["version"]

[tool.hatch.version]
source = "vcs"
```

Avoid a static version. It produces a byte-identical wheel filename on every
build, so any channel that advertises a URL rather than a version — the nightly
manifest, say — leaves uv finding the requirement already satisfied and skipping
the download. It also makes the plugin unpublishable, since PyPI rejects
re-uploading a version.

What a plugin *does* depend on is the host API it targets. `precursor` ships a
`py.typed` marker, so `precursor.plugin_api` type-checks against whatever
version is installed; state the minimum host you support in your README, and
don't depend on `precursor-ai` from your `[project] dependencies` — the host
imports you, and `precursor-ai[<your-extra>]` would become a cycle.

## Stability

The contract may evolve before 1.0. We keep breakage to a minimum, bump
`PLUGIN_API_VERSION` / `HOST_API_VERSION`, and call it out in release notes.
