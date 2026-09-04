---
title: Workflows reference
---

# Workflows reference

The `/api/workflows` surface, and how a pipeline travels between installs.

::: tip Generating a workflow with an AI assistant
This page documents the surface for a human reader. If you're pointing a coding
assistant at Precursor and asking it to *produce* a workflow, give it the
[workflow authoring spec](/reference/workflow-authoring) instead — a
machine-oriented specification with the exact field tables, placeholder grammar,
gate verdict parsing and validation rules.
:::

## Sharing a workflow

A workflow can be **exported to YAML** — its steps, its wiring, and the agents
those steps use — then imported elsewhere. On import, any agent whose name
already exists gives you the choice to reuse it, replace it, or keep both. See
[import & export](/features/transfer).

Two things deliberately don't travel: **webhook tokens** (per-install
credentials) and a **live schedule**, which arrives paused so a shared file can't
start firing on its new owner. A step's
[tool-server allowlist](/features/workflows/steps#picking-which-tool-servers-a-step-gets)
*does* travel, and the import preview warns about any server the receiving
machine can't attach.

## API surface

Workflows live under `/api/workflows`:

| Method & path | Purpose |
| --- | --- |
| `GET /api/workflows` | List workflows (`?includeArchived`) |
| `POST /api/workflows` | Create a workflow with steps |
| `GET /api/workflows/{id}` | Fetch one |
| `GET /api/workflows/{id}/runs` | List persisted run traces (`?limit`) |
| `PATCH /api/workflows/{id}` | Update fields / replace steps |
| `DELETE /api/workflows/{id}` | Delete |
| `POST /api/workflows/{id}/run` \| `/pause` \| `/resume` \| `/cancel` | Lifecycle. `run` and `resume` take an optional `{ "input": "…" }` — a run brief, and an answer to whatever blocked the step |
| `POST /api/workflows/{id}/retry` | Re-drive one step of a stopped run (`{ "position": N, "input": "…" }`) |
| `POST /api/workflows/{id}/permission` | Answer a step's tool-permission gate (`{ "request_id": "…", "decision": "approve-once\|approve-always\|deny" }`) and resume the run |
| `GET /api/workflows/{id}/run-steps/{stepRunId}/events` | One attempt's agent activity (tool calls, reasoning) |
| `POST /api/workflows/{id}/run-steps/{stepRunId}/replay` | [Replay](/features/workflows/running#replaying-a-single-step) one attempt on its recorded input, advancing nothing (409 while the run is in flight) |
| `POST /api/workflows/{id}/approve` \| `/reject` | Clear or bounce a human approval checkpoint (`{ "note": "…", "action": "rework\|stop\|skip" }`) |
| `POST /api/workflows/{id}/archive` \| `/unarchive` | Archive toggle |
| `PUT /api/workflows/{id}/schedule` | Configure the schedule. Accepts either the flat recurrence fields or a `rules` array for [several cadences at once](/features/scheduler#several-cadences-on-one-item) |
| `GET /api/workflows/{id}/state` | List the pipeline's [saved state](/features/workflows/steps#pipeline-state-what-a-workflow-remembers) |
| `PUT /api/workflows/{id}/state` | Upsert one value (`{ "key": "…", "value": "…" }`) |
| `DELETE /api/workflows/{id}/state/{key}` \| `/state` | Drop one key, or reset the lot |
| `POST` \| `DELETE /api/workflows/{id}/webhook` | Mint / revoke a webhook token |
| `POST /api/workflows/hooks/{token}` | Trigger via webhook (body → run brief) |
| `GET /api/transfer/workflows/{id}` | [Export](/features/transfer) the workflow (+ its agents) as YAML |

Lifecycle changes broadcast a `workflow.changed` [SSE event](/reference/api#real-time-events)
so the dashboard live-updates without polling.

Every workflow read carries a `run_progress` object — the newest run's `done` /
`total` positions, its `status`, and the `current_position` in flight (null until
the workflow has ever run). That's what lets the gallery
[draw a progress bar per card](/features/workflows/running#watching-several-pipelines-at-once)
without loading a run trace for each one.

## MCP tools

An agent running a step can read and write its pipeline's
[state](/features/workflows/steps#pipeline-state-what-a-workflow-remembers)
through Precursor's own MCP server — `workflow_state_list`,
`workflow_state_get`, `workflow_state_set` and `workflow_state_delete`. Each
resolves the owning workflow **per call** rather than from the environment,
which is the right answer for an agent shared by several pipelines.

Serving these to *external* MCP hosts is a separate opt-in under
**Settings → MCP servers → Precursor capabilities → Workflow state**.
