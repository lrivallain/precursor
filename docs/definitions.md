# Agent & workflow definition files

> **Work in progress.** This page describes the file format for declaring
> agents and workflows, the folder check, the one-shot export of today's agents
> and workflows into files, and an opt-in **files mode** in which the runtime
> reads them. By default Precursor still runs everything from the database. The
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
| Ad-hoc agents (`/agent <task>` in a topic or chat) | Get a file like every other agent, under `agents/adhoc/`, so there is a single way to read a declaration. |
| Archive | A database flag only; the file is left in place so paths and references stay stable. |
| Roles | Referenced by name (`role: Analyst`), matched case-insensitively. A name that doesn't exist on the instance is a **warning** in the check, not an error. Roles themselves stay in the database for now. |

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
| `on_fail` | Gate / approval: the step to re-drive on FAIL or rework — any other step, including an approval step ("on FAIL, ask a human"). Default: the previous runnable step. |
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
  `on_fail` pointing at a missing step or at itself, and a `context.from`
  naming a missing or *later* step are all errors.
- **Duplicate YAML keys are errors.** Plain YAML keeps the last of two
  `prompt:` keys without a word; here that's reported.
- **Agent paths** must be clean, relative, forward-slash paths ending in
  `.agent.yaml` — no leading `/`, no `..`, no `./`.
- A file written by a newer Precursor (`format` greater than this version
  supports) is refused with an "upgrade" message.

Checks that need the whole folder or the instance are described under
[checking a folder](#checking-a-folder).

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

## The definitions folder

Precursor looks for definition files under **`<data dir>/definitions`**, or the
folder named by `PRECURSOR_DEFINITIONS_DIR`. Every `*.agent.yaml` and
`*.workflow.yaml` below it is read, in any sub-folder; hidden folders (such as
`.git`) and hidden files are skipped. A symlink is followed only if it stays
inside the folder.

## Checking a folder

The check runs every rule above per file, then across the folder:

| Finding | Severity |
| --- | --- |
| A file that doesn't parse or validate (with the line, or the step key, at fault) | error |
| The same `id` in two files | error, on both files |
| A step's `agent:` path that points at no file (with a *did you mean* when a file of that name exists elsewhere) | error |
| A step whose agent file has errors | error |
| A role name that doesn't exist on this instance | warning |
| An MCP server name that isn't configured on this instance | warning |
| Database agents or workflows with no file yet | warning |

Warnings never fail the check: a definition is portable, so the same file can
be right on another machine. Model names are not checked — the model catalogue
depends on the provider and the network.

There are two ways to run it:

- **From the app:** `GET /api/definitions/check` checks the configured folder
  against the running instance — roles, MCP servers, and which database rows
  are linked to a file. Each file in the report carries its SHA-256, the value a
  run will record as "the version used" from roadmap step 4.
- **From a terminal or CI:** `precursor validate [FOLDER] [--json]` needs no
  database, so it runs the file and cross-file rules only. It exits `0` with no
  errors, `1` with errors, and `2` when the folder doesn't exist.

```console
$ precursor validate ~/my-definitions
error: workflows/morning-briefing.workflow.yaml: steps[triage].agent: no agent file at agents/inbox-triage.agent.yaml; paths are relative to the definitions folder
Checked 3 files in /Users/me/my-definitions: 1 error, 0 warnings.
```

## Exporting today's agents and workflows

`POST /api/definitions/export` writes a file for every agent and workflow in
the database that doesn't have one yet (Agents mode must be on). Nothing is
deleted, and the database stays authoritative until the runtime switches over.

- A reusable agent goes to `agents/<title>.agent.yaml`; one spawned from a topic
  or chat to `agents/adhoc/`. Archived ones are exported too.
- A workflow goes to `workflows/<name>.workflow.yaml`. A step's private
  (inline) agent gets no file: its prompt and model move into the step. Step
  keys are derived from the step's label.
- Each row is **linked** to its file through its portable id (`export_id`),
  which becomes the file's `id`. An agent that was already shared through a
  transfer file keeps the id it had.
- Running it again skips every row that already has a file, so hand edits are
  safe. `?overwrite=true` regenerates those files from the database, in place.

Values the format would reject are normalised with a warning, and settings a
file can't hold are reported rather than dropped silently:

- A step's hidden agent may have its own role, approval policy, autonomy or
  limits, which a step prompt can't carry. That's only reported when it would
  actually stop applying: the workflow's own role and approval policy replace
  the agent's for every step anyway.
- A `selected` context source that isn't an earlier step is left out; a
  selection left with no earlier step is written as `mode: none`, which is what
  it did.
- A step whose agent was deleted can't be expressed at all. The workflow file
  is still written, with that step as-is, and reported as an error to fix by
  hand — dropping the step would shift the position-based `{{step.N.output}}`
  placeholders after it.

To try it on a copy of real data, point a dev instance at a snapshot of your
database and a scratch folder:

```bash
PRECURSOR_DATABASE_URL="sqlite+aiosqlite:////tmp/snap.db" \
PRECURSOR_DEFINITIONS_DIR=/tmp/defs \
  uv run --frozen precursor --dev
curl -s -X POST localhost:8000/api/definitions/export | jq '.written | length'
curl -s localhost:8000/api/definitions/check | jq '{ok, error_count, warning_count, database}'
```

## Files mode

Set `PRECURSOR_DEFINITIONS_SOURCE=files` (default `database`) and restart.
Every agent and workflow **linked to a file** is then declared by that file.

- **Linked means:** a listed agent or a workflow whose portable id (`export_id`)
  is a file's `id`, which is what the export sets up. A step's own prompt is
  linked through its workflow file and step key.
- **Where it applies:** everywhere the row is read — runs, the API and the UI,
  search, the MCP server. The database columns are left untouched; they are
  simply no longer used for a linked row.
- **Edits on disk** show up within a second.
- **A row with no file** keeps running from the database, so a new agent
  created in the app still works; the check lists it, and the export writes its
  file.
- **A file with errors** is never silently replaced by the old database copy:
  starting that agent fails with the reason, and running that workflow is
  refused with a `409`.

Each run records which file it ran from and that file's SHA-256 at the time
(`definition_path` and `definition_hash` on agent runs and workflow runs). The
settings a run already snapshots when it opens — model, capabilities, role,
approval policy — are taken from the file at that moment. The others (prompt,
autonomy, limits) are read as the run goes, exactly as they are from the
database.

### Workflow steps

A workflow's step rows stay in the database, but only as *anchors*: their
position, the agent they run and the per-run counters. Each is tied to a file
step by `<workflow id>/<step key>`, and everything the step declares comes from
the file. When the file's step list changes, the rows follow — added, removed,
reordered, re-pointed — the next time the workflow is read or started:

- The first time an exported workflow is read from its file, its existing rows
  are adopted by position, so each step keeps its private agent and history.
- A prompt step keeps its private agent while its key stays the same.
- A step whose agent file has no row yet (a file written by hand, or copied
  from another machine) gets one, declared by the file.
- A removed prompt step's private agent is deleted with it.
- **Never mid-run:** a running, paused or waiting workflow keeps the steps it
  started with; the file's new shape applies from the next run.

::: warning Not yet
Editing an agent or workflow **in the app** still writes the database, so in
files mode those edits are overridden by the file. Edit the files instead until
the app writes them (roadmap step 6).
:::

## Roadmap

Each step ships on its own and is validated before the next starts.

0. ✅ Agree the file / database boundary.
1. ✅ File format and JSON Schema.
2. ✅ Definitions folder, read-only loader and folder-wide integrity check
   (API + `precursor validate` CLI). Not used at runtime yet.
3. ✅ Export existing agents and workflows to files, linking each row to its
   file through `export_id`. Nothing deleted.
4. ✅ Agents and workflows run from their files (files mode); each run
   records the file version it used.
5. The Agents and Workflows lists come from the files.
6. Editing in the app writes the file (comment-preserving).
7. Permission changes arriving from disk (approval policy, MCP scope,
   autonomy) are held until a human accepts them; the assistant's file tools
   cannot write the definitions folder.
8. The definitions folder can be a git workspace: pull, check, accept, push.
9. The old declaration columns are removed from the database.
