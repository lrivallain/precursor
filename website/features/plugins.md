---
title: Plugins
---

# Plugins

Precursor is intentionally small. Anything that isn't part of "topics, chat,
GitHub" is meant to live in a **plugin**. Plugins extend both halves of the app —
the FastAPI backend and the React SPA — without forking core.

This page is a feature overview; for the full contract and API, see the
[plugin reference](/reference/plugins).

## Backend plugins

A backend plugin is a Python package that exposes a `register(registry)` callable
and declares it as an entry point:

```toml
# pyproject.toml of the plugin package
[project.entry-points."precursor.plugins"]
my_plugin = "my_pkg.precursor_plugin:register"
```

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

Plugins are discovered once on startup; a failing plugin is **logged and never
crashes the host**.

## Frontend extensions

The SPA fetches `/api/plugins` on boot and stores the descriptors. Extensions
describe themselves **declaratively** (kind + slot + config); to render one, you
register a renderer for its `kind`:

```ts
import { registerRenderer } from "./lib/plugins";
import { MyPanel } from "./my-plugin/MyPanel";

registerRenderer("panel", MyPanel);
```

## Designed plugin kinds

| Kind | Slot examples | Use case |
| --- | --- | --- |
| `panel` | `topic.sidebar.bottom` | Side-by-side context (e.g. a PR diff). |
| `message-renderer` | `chat.message.body` | Mermaid / drawio / chart blocks. |
| `settings-tab` | `settings.tabs` | Per-plugin configuration UI. |
| `topic-action` | `topic.header.actions` | Buttons that operate on the topic. |

::: info Stability
The contract is stable, but slots and dynamic renderer mounting are being wired
in progressively — see the [plugin reference](/reference/plugins) for what's live
today and the roadmap.
:::
