---
title: Plugin reference
---

# Plugin reference

This is the detailed contract for extending Precursor. For a friendly overview,
see the [Plugins feature guide](/features/plugins).

::: info Evolving contract
The contract is stable, but not every slot and dynamic renderer mount is wired in
core yet — the rendering call sites land as the first plugins ship. Breakage
before 1.0 will be kept to a minimum and called out in release notes.
:::

## Design principle

Precursor is intentionally small. Anything that is **not** part of "topics, chat,
GitHub" should live in a plugin rather than growing core.

## Backend contract

A plugin is a Python package that exposes a `register(registry)` callable and
declares it as an entry point in the `precursor.plugins` group:

```toml
# pyproject.toml of the plugin package
[project.entry-points."precursor.plugins"]
my_plugin = "my_pkg.precursor_plugin:register"
```

The `register` function receives a `PluginRegistry` and can:

- **mount routers** — `registry.add_router(router)` adds a FastAPI `APIRouter`
  (namespace it under `/api/<your-plugin>` to avoid collisions), and
- **contribute frontend extensions** — `registry.add_frontend_extension(...)`
  registers a descriptor served from `/api/plugins`.

```python
# my_pkg/precursor_plugin.py
from fastapi import APIRouter
from precursor.backend.plugins import FrontendExtension, PluginRegistry

router = APIRouter(prefix="/api/my-plugin", tags=["my-plugin"])


@router.get("/ping")
async def ping():
    return {"ok": True}


def register(registry: PluginRegistry) -> None:
    registry.add_router(router)
    registry.add_frontend_extension(
        FrontendExtension(
            id="my-plugin.panel",
            kind="panel",
            slot="topic.sidebar.bottom",
            title="My panel",
            config={"endpoint": "/api/my-plugin/ping"},
        )
    )
```

Plugins are discovered once on startup by `discover()` (called from the FastAPI
lifespan). A failing plugin is **logged and never crashes the host**.

## Frontend contract

The SPA fetches `/api/plugins` on boot and stores the descriptors. To render a
contributed extension, register a renderer for its `kind`:

```ts
// frontend/src/main.tsx (in your fork or a separate bundle)
import { registerRenderer } from "./lib/plugins";
import { MyPanel } from "./my-plugin/MyPanel";

registerRenderer("panel", MyPanel);
```

`MyPanel` receives the `descriptor` (`id`, `slot`, `title`, `config`) and is
mounted wherever the SPA renders that slot.

## `FrontendExtension` descriptor

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | string | Stable unique id (e.g. `my-plugin.panel`). |
| `kind` | string | Which renderer handles it (see below). |
| `slot` | string | Where in the SPA it mounts. |
| `title` | string | Human-readable label. |
| `config` | object | Free-form config passed to the renderer (e.g. an endpoint). |

## Designed plugin kinds

| Kind | Slot examples | Use case |
| --- | --- | --- |
| `panel` | `topic.sidebar.bottom` | Side-by-side context (e.g. a PR diff). |
| `message-renderer` | `chat.message.body` | Mermaid / drawio / chart blocks. |
| `settings-tab` | `settings.tabs` | Per-plugin configuration UI. |
| `topic-action` | `topic.header.actions` | Buttons that operate on the topic. |

## MCP tools from a plugin

Because Precursor is an [MCP client](/features/mcp), a plugin can also register
external MCP tool servers, so a plugin can contribute new tools the assistant can
call without touching core.
