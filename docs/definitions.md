# Agent & workflow definition files

> **Work in progress — format only.** This page describes the file format for
> declaring agents and workflows. Precursor does not read these files yet: the
> app still stores agents and workflows in the database. The
> [roadmap](#roadmap) lists the steps that get it there, and each one ships on
> its own.

## The idea

- **A file says what an agent or a workflow *is*:** prompt, model, role,
  tools and MCP servers, approval policy, limits, and the workflow's steps and
  their policies. Files are the **only** source of truth for these
  declarations.
- **The database holds what changes while it runs:** status, progress, runs,
  attempts, events and artifacts, token spend, retries, **scheduling**,
  **triggers**, webhook tokens, workflow state and read markers.

Files can then live in a git repository: history, diffs and review come for
free, the same definitions can be used on several machines, and any editor
(or the assistant itself) can write them.

## Decisions (step 0)

| Question | Decision |
| --- | --- |
| Identity | Every file has a stable `id:`. The database row links to it through the existing `export_id` column, so renaming or moving a file keeps its run history. The path is only used to *reference* a file. |
| Schedules | Stay in the database (cadence, enabled switch, next run). A schedule does not travel with a file. |
| Triggers | Stay in the database — the webhook token is a secret and must never reach a git-tracked file. |
| Ad-hoc agents (`/agent <task>` in a topic or chat) | Get a file like every other agent, under `agents/adhoc/`, so there is a single way to read a declaration. *Provisional.* |
| Archive | A database flag; the file is left in place so paths and references stay stable. *Provisional.* |
| Roles | Referenced by name (`role: Analyst`). Roles themselves stay in the database for now. |

Out of scope for now: roles, blueprints and the MCP server registry stay in
the database; skills are already files.

## Folder layout

```text
<definitions folder>/
├── agents/
│   ├── inbox-triager.agent.yaml
│   └── adhoc/            # agents spawned from a topic or chat
└── workflows/
    └── morning-briefing.workflow.yaml
```

A workflow refers to an agent by its path **relative to the definitions
folder** (`agent: agents/inbox-triager.agent.yaml`), not relative to the
workflow file.

Complete examples live in [`docs/examples/definitions/`](examples/definitions/).

## Agent files — `*.agent.yaml`

```yaml
kind: agent
id: inbox-triager            # never change it
title: Inbox triager
prompt: |
  Sort every thread from the last 24 hours into act now / this week / later.
model: gpt-5.4               # omit for the default model
role: Analyst                # an Assistant Role, by name
approval_policy: balanced    # manual | balanced | autonomous; omit to inherit
autonomy:
  enabled: false
  max_steps: 12
capabilities:
  mcp: true
  skills: true
  memory: true
  mcp_servers: [workiq]      # omit = every enabled server, [] = none
limits:
  token_budget: 200000       # cumulative, across runs
  max_retries: 0
```

Only `kind`, `id` and `title` are required; everything else has the same
default as an agent created in the app.

## Workflow files — `*.workflow.yaml`

```yaml
kind: workflow
id: morning-briefing
name: Morning briefing
description: Triage, write, check, then wait for a human OK.
icon: sunrise
color: amber
role: Analyst                # applied to every step
approval_policy: autonomous  # applied to every step; omit to keep each agent's own
clear_artifacts: true
max_loops: 3
step_timeout_seconds: 1800
steps:
  - key: triage
    agent: agents/inbox-triager.agent.yaml
    on_error: retry
    max_retries: 2
  - key: brief
    agent: agents/brief-writer.agent.yaml
    instructions: Focus on the "act now" threads.
    context:
      mode: selected
      from: [triage]
  - key: check
    kind: gate
    prompt: PASS if every point is backed by a thread, else FAIL and say why.
    on_fail: brief
  - key: review
    kind: approval
    on_reject: rework
```

### Steps

Every step has a `key`: lowercase letters, digits, `-` and `_`, unique within
the workflow. Other steps refer to it by that key, so reordering the list
never re-points a reference.

| `kind` | Runs | Needs |
| --- | --- | --- |
| `task` (default) | an agent | `agent:` **or** its own `prompt:` |
| `inline` | a one-off prompt owned by the step | `prompt:` |
| `gate` | a judge voting PASS / FAIL; FAIL loops back to `on_fail` | `agent:` **or** `prompt:` |
| `approval` | nothing — waits for a human | neither |

| Field | Meaning |
| --- | --- |
| `name` | Display label; defaults to the agent's title. |
| `model` | Model for the step's own `prompt`. For an agent step, set it in the agent file. |
| `instructions` | Extra mandate for this step only. Supports `{{run.input}}`, `{{state.<key>}}` and `{{step.N.output}}` (N is the 0-based position for now). |
| `on_fail` | Gate / approval: the step to re-drive on FAIL or rework. Default: the previous step that runs an agent. |
| `on_error` / `max_retries` | `fail` (default), `retry` (with `max_retries`), or `continue`. |
| `on_reject` | Approval only: `rework` (default), `stop` or `skip`. |
| `context` | `mode: auto` (previous output + artifacts, default), `selected` (only the steps listed in `from`), or `none`. |
| `capabilities` | Per-step `mcp` / `skills` / `memory` / `mcp_servers`; an omitted toggle inherits the agent. |

## Validation

Files are checked strictly, because they are written by hand:

- **Unknown keys are errors.** `instuctions:` fails instead of silently
  dropping the mandate.
- **Contradictions are errors:** a step with both `agent` and `prompt`, an
  approval step with an agent, `model` on an agent step, `max_retries` without
  `on_error: retry`, `on_reject` outside an approval step, `mcp_servers` with
  `mcp: false`, `from` without `mode: selected`.
- **References inside a workflow must resolve:** duplicate step keys,
  `on_fail` pointing at a missing step, at itself or at an approval step, and
  a `context.from` naming a missing or *later* step are all errors.
- **Agent paths** must be clean, relative, forward-slash paths ending in
  `.agent.yaml` — no leading `/`, no `..`, no `./`.
- A file written by a newer Precursor (`format` greater than this version
  supports) is refused with an "upgrade" message.

Checks that need the whole folder — duplicate `id`s across files, an agent
path that points at no file, unknown role, model or MCP server names — come
with the folder checker (roadmap step 2).

### Editor support

JSON Schemas for both kinds are committed under
[`docs/schemas/`](schemas/). Point the VS Code YAML extension (or any
`yaml-language-server` client) at them with a modeline to get completion and
inline errors. The path is relative to the file, as in the examples:

```yaml
# yaml-language-server: $schema=../../../schemas/agent.schema.json
```

The JSON Schema covers structure, types and allowed values. The cross-field
rules above (for example "an approval step has no agent") are enforced by
Precursor itself.

The schemas are generated from the Pydantic models in
`precursor/backend/schemas/definitions.py`; after changing a model, run:

```bash
uv run --frozen python scripts/gen_definition_schemas.py
```

`tests/test_definitions.py` fails if they drift.

## Roadmap

Each step ships on its own and is validated before the next starts.

0. ✅ Agree the file / database boundary.
1. ✅ File format and JSON Schema (this page).
2. Definitions folder, read-only loader and folder-wide integrity check
   (API + `precursor validate` CLI). Not used at runtime yet.
3. Export existing agents and workflows to files, linking each row to its
   file. Nothing deleted.
4. Agents and workflows run from their files; each run records the file
   version it used.
5. The Agents and Workflows lists come from the files.
6. Editing in the app writes the file (comment-preserving).
7. Permission changes arriving from disk (approval policy, MCP scope,
   autonomy) are held until a human accepts them; the assistant's file tools
   cannot write the definitions folder.
8. The definitions folder can be a git workspace: pull, check, accept, push.
9. The old declaration columns are removed from the database.
