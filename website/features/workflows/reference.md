---
title: Workflows reference
---

# Workflows reference

Sharing a workflow, its HTTP API and its MCP tools. For exact field tables,
placeholder grammar and validation rules (also what to hand a coding assistant
that must generate a workflow), see the
[workflow authoring spec](/reference/workflow-authoring).

## Sharing a workflow

Export a workflow to YAML (its steps and the agents they use) and import it
elsewhere from **Workflows → Import**. When an agent name already exists you can
reuse it, replace it, or keep both. See [import & export](/features/transfer).

- **Not carried:** webhook tokens, and the schedule's *enabled* state (a carried
  schedule arrives paused).
- **Carried:** each step's
  [tool-server allowlist](/features/workflows/steps#picking-which-tool-servers-a-step-gets);
  the import preview warns about servers this machine can't attach.

## API surface

Workflows live under `/api/workflows`. The full route table, request bodies and
error codes are in the authoring spec's
[REST surface](/reference/workflow-authoring#rest-surface). Two things a client
usually needs:

- Lifecycle changes broadcast a `workflow.changed`
  [SSE event](/reference/api#real-time-events), so there is no need to poll.
- Every workflow read includes `run_progress` (null if the workflow never ran):
  the newest run's `run_number`, `status`, `done` / `total` positions and
  `current_position` (null when nothing is in flight). It drives the [gallery progress bars](/features/workflows/running#watching-several-pipelines-at-once).

## MCP tools

Steps read and write [pipeline state](/features/workflows/steps#pipeline-state-what-a-workflow-remembers)
with `workflow_state_list`, `workflow_state_get`, `workflow_state_set` and
`workflow_state_delete` on Precursor's own MCP server. `workflow_id` defaults to
the workflow currently running the calling agent (resolved per call, so a shared
agent writes to the right pipeline); pass it explicitly otherwise.

To serve these tools to external MCP hosts, enable **Workflow state** under
**Settings → MCP servers → Precursor capabilities**.
