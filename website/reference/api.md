---
title: API reference
---

# API reference

::: info Coming soon
A full, generated API reference is on the roadmap. This page will document the
`/api/*` surface — endpoints, request/response schemas, and the SSE event stream —
generated from FastAPI's OpenAPI schema so it stays in lockstep with the code.
:::

Until then, the fastest way to explore the API is the **interactive OpenAPI docs**
that FastAPI serves for a running instance.

## Interactive docs (live instance)

With Precursor running locally, open:

- **Swagger UI** — [`/api/docs`](http://127.0.0.1:8000/api/docs)
- **ReDoc** — [`/api/redoc`](http://127.0.0.1:8000/api/redoc)
- **OpenAPI schema (JSON)** — [`/api/openapi.json`](http://127.0.0.1:8000/api/openapi.json)

(Adjust the port to match your `--port`.)

::: tip
FastAPI's interactive docs live under `/api/*` so the root `/docs` path can
serve this documentation site **in-app** — see
[Serving the docs in-app](#serving-the-docs-in-app) below.
:::

## Serving the docs in-app

This documentation site (the VitePress project in `website/`) is also served by
the app itself at **[`/docs/`](http://127.0.0.1:8000/docs/)**, so you can read it
without leaving Precursor (there's a **Documentation** entry in the command
palette and the About dialog).

- **Production / one-port `precursor`** — the site is pre-built with base
  `/docs/` (`make docs`) and bundled into the wheel; the backend serves it
  statically at `/docs/*`, resolving VitePress clean URLs.
- **`precursor --dev`** — a live VitePress dev server runs on a hidden port and
  the SPA's Vite proxies `/docs` to it, so editing any `website/**` markdown
  **hot-reloads** in the browser.
- **GitHub Pages** is unaffected: it builds the same source with the default
  base `/` in its own workflow.

## Surface at a glance

The JSON API lives under `/api/*`. Routers are grouped by domain:

| Area | What it covers |
| --- | --- |
| `topics` | CRUD for topics, the topic tree, and per-topic messages. |
| `chat` | Streamed chat (`.../messages/stream`) over Server-Sent Events. |
| `chats` | Quick throwaway chats. |
| `settings` | Runtime settings and provider/GitHub configuration (secrets never echoed). |
| `github` | Issue/label/comment operations behind topic linking. |
| `mcp` | Tool-server registry, enable/disable, and OAuth (re)authentication. |
| `skills` / `memories` | Skill enablement and long-term memory. |
| `schedules` | Topic/agent recurrence and **Run now**. |
| `agents` | Agent sessions, timelines, and read/unread state. |
| `stt` | Short-lived Azure Speech token minting for live sessions. |
| `plugins` | Descriptors for frontend extensions contributed by plugins. |

Health and version:

- `GET /api/health` — liveness + version.
- `GET /api/version` — the CalVer version (derived from git tags at build time).

## Real-time events

- `GET /api/events` — a Server-Sent Events stream the SPA subscribes to for
  cross-window sync (`message.changed`, `stream.ended`, `mcp.auth_url`,
  `mcp.auth_required`, `mcp.auth_resolved`, …).
- Streamed chat responses are their own SSE stream, delivering text deltas and
  tool-call events for a single turn.

::: tip Contributions welcome
Want to help build the generated reference? See the
[contribution guide](/contributing/).
:::
