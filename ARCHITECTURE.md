# Architecture and reuse map

Where code goes in Precursor, and the building blocks to reuse before writing
new ones. Read this before adding a feature; update it when you add a building
block others should reuse.

The rule of thumb: **look for an existing helper first, extend it if it almost
fits, and only then write a new one, in the shared place listed here.** Copying
a block from a neighbouring file is the one option that is never right; the
quality ratchet (below) will flag it.

## Layout

```
precursor/backend/
  main.py            app factory + lifespan (starts/stops background components)
  config.py          env settings (get_settings); per-user settings live in the DB
  db.py              engine, SessionLocal, get_session
  lookups.py         get_or_404
  models/            SQLAlchemy models (Alembic owns the schema)
  schemas/           Pydantic models: the single source of truth for API shapes
  routers/           thin FastAPI routers: validate, call a service, return a schema
  services/          the logic; one module per concern
    agents/          agents mode: AgentManager (manager.py) delegates each concern to a
                     collaborator (runs, session_pool, turns, event_handling, goal_loop,
                     watchdog, timeline, permissions, ...)
      workflow_engine/   the workflow coordinator, one module per concern; workflow.py re-exports it
    mcp/             MCP client (client.py) and built-in servers
      precursor_tools/   tools of the built-in "precursor" server, one module per group
frontend/src/
  lib/               API client, stores, hooks, pure helpers
    generated/       apiTypes.ts, generated from the backend (never edit)
  components/        React components; a feature with several files gets a folder
    settings/        the settings panel's tabs, state hook and cards
tests/               pytest suite (backend); frontend unit tests sit next to the code (*.test.ts)
scripts/             CI and maintenance scripts (stdlib-only where they run in hooks)
```

## Backend building blocks

| Need | Reuse | Where |
| --- | --- | --- |
| Fetch a row by id or return 404 | `get_or_404(session, Model, id, "X not found")` | `lookups.py` |
| Same, as a route dependency | `get_topic_or_404`, `get_chat_or_404` | `routers/deps.py` |
| DB session in a route | `Depends(get_session)` | `db.py` |
| DB session outside a request (tasks, streams) | `async with SessionLocal() as session` | `db.py` |
| A per-user setting stored in the DB | a `resolve_*` function | `services/app_settings.py` |
| A background job that wakes every N seconds | subclass `BackgroundPoll`; expose `get_x = X.instance`; add it to the lifespan list in `main.py` | `services/background_poll.py` |
| An age-limited cleanup sweep | `cutoff`, `replace_column`, `delete_rows`, `rowcount`, `SessionFactory` | `services/retention_sweep.py` |
| Report what a sweep did | `SweepResult` | `services/sweep_result.py` |
| Prepare a conversation turn (topic or chat) | `conversation_turn.py`; persist the reply with `persist_final_turn` (`turn_engine.py`) | `services/` |
| A new AgentManager concern | a collaborator class in `services/agents/` holding a `manager` back-reference (only a `TYPE_CHECKING` import of the manager), with a same-named delegate on `AgentManager` | `services/agents/` (e.g. `turns.py`) |
| A tool on the built-in MCP server | `@mcp.tool()` in the matching `precursor_tools/<group>.py`, gated with `_section_enabled`; new groups are added to `precursor_tools/__init__.py` | `services/mcp/precursor_tools/` |
| Links to topics, chats and messages | `app_base_url`, `topic_paths`, `message_path` | `services/links.py`, `services/message_links.py` |

Conventions that keep this reusable:

- Routers stay thin. Anything a second router, a ticker or an MCP tool could
  need goes in `services/`.
- Every JSON response declares a Pydantic `response_model`. A `dict[str, Any]`
  response gives the frontend no type (it is counted by the ratchet).
- Use `Literal` for fields with a fixed set of values, so the generated
  frontend type is exact.

## Frontend building blocks

| Need | Reuse | Where |
| --- | --- | --- |
| Call the API | `api.*` | `lib/api.ts` |
| Stream a chat reply | `streamChat` and friends (POST + SSE) | `lib/sse.ts` |
| Types of what the API sends and accepts | aliases of the generated types (`export type X = Api.X`) | `lib/types.ts`, `lib/generated/apiTypes.ts` |
| A store shared across components | extend `VersionedStore` (class) or use `Emitter` (module functions); read it with `useStoreVersion` | `lib/externalStore.ts` |
| A list loaded once from the API (roles, skills...) | extend `ListResourceStore<T>` | `lib/externalStore.ts` |
| A resizable panel | `useResizableWidth` / `useResizableHeight` / `useResizableBox` + `components/ResizeHandle.tsx` | `lib/` |
| A new kind of resize or a value persisted in localStorage | `usePersistedState`, `useMouseDrag`, `clamp` | `lib/resizeCore.ts` |
| Hover hints | `data-tooltip="..."` (not `title`) | `components/Tooltip.tsx` |
| A confirmation dialog | `useConfirm()` | `components/ConfirmDialog.tsx` |
| A dropdown | `Select` | `components/Select.tsx` |
| A settings tab | a component in `components/settings/` taking `{ s: SettingsPanelState }` | `components/settings/useSettingsPanelState.ts` |

Conventions that keep this reusable:

- A component holds layout; state and effects that are more than a few lines
  go in a `use*` hook in `lib/` (or next to the component when only it uses
  them).
- Pure logic (parsing, formatting, tree walks) lives in a `lib/` module with no
  React import, and gets a `*.test.ts` next to it.

## API types

The backend's Pydantic schemas are the single source of truth for API shapes.
`make api-types` turns the app's OpenAPI schema into
`frontend/src/lib/generated/apiTypes.ts`; CI fails when that file is stale.
After changing a schema or a route's `response_model`, regenerate and commit.
Never edit the generated file; hand-write types in `types.ts` only for
frontend-only shapes.

## Limits and the quality ratchet

New code must stay under these limits. Existing code that exceeds them is
recorded in `scripts/quality_baseline.json` and may only shrink
(`make ratchet`, details in `CONTRIBUTING.md`).

| Limit | Value |
| --- | --- |
| File length (`.py`, `.ts`, `.tsx`) | 800 lines |
| Python function | complexity 15, 15 branches, 60 statements |
| TypeScript function | complexity 15, 150 lines, nesting depth 4 |
| Duplication | no new copy-pasted block of 8+ lines |
| JSON responses | always a declared model |

When you are about to cross a limit, split along the existing seams: a
component per tab or section, a hook per concern, a service module per
responsibility, a `precursor_tools` module per tool group.
