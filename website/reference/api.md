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

- **Swagger UI** — [`/docs`](http://127.0.0.1:8000/docs)
- **ReDoc** — [`/redoc`](http://127.0.0.1:8000/redoc)
- **OpenAPI schema (JSON)** — [`/openapi.json`](http://127.0.0.1:8000/openapi.json)

(Adjust the port to match your `--port`.)

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
  `mcp.auth_required`, …).
- Streamed chat responses are their own SSE stream, delivering text deltas and
  tool-call events for a single turn.

::: tip Contributions welcome
Want to help build the generated reference? See the
[contribution guide](/contributing/).
:::
