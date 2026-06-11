# Plugins

Precursor is intentionally small. Anything that is not part of "topics, chat,
GitHub" should live in a plugin.

## Backend

A plugin is a Python package that exposes a `register(registry)` callable and
declares it as an entry point:

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

Plugins are discovered once on startup by `discover()` (called from the
FastAPI lifespan). Failures are logged and never crash the host.

## Frontend

The SPA fetches `/api/plugins` on boot and stores the descriptors. To render
a contributed extension, register a renderer for its `kind`:

```ts
// frontend/src/main.tsx (in your fork or a separate bundle)
import { registerRenderer } from "./lib/plugins";
import { MyPanel } from "./my-plugin/MyPanel";

registerRenderer("panel", MyPanel);
```

`MyPanel` receives the `descriptor` (id, slot, title, config) and is mounted
wherever the SPA renders that slot.

> **Note** — slots and dynamic renderer mounting are designed but not all
> wired in the initial release. The contract is stable; the rendering call
> sites will land as the first plugins ship.

## Roadmap

Designed plugin kinds (none implemented in core today):

| Kind                | Slot examples                  | Use case                            |
| ------------------- | ------------------------------ | ----------------------------------- |
| `panel`             | `topic.sidebar.bottom`         | Side-by-side context (e.g. PR diff) |
| `message-renderer`  | `chat.message.body`            | Mermaid / drawio / chart blocks     |
| `settings-tab`      | `settings.tabs`                | Per-plugin configuration UI         |
| `topic-action`      | `topic.header.actions`         | Buttons that operate on the topic   |

## Stability

The contract may evolve before 1.0; we'll keep breakage to a minimum and call
it out in release notes.
