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
| `instructions` | Extra mandate for this step only. Supports `{{run.input}}`, `{{state.<key>}}` and `{{step.<key>.output}}` — another step's output, by key, so the reference survives a reorder. `{{step.N.output}}` (0-based position) still works; the export rewrites it by key, and the check warns about a key the workflow doesn't have. |
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

Precursor looks for definition files under **`<data dir>/definitions`**, the
folder named by `PRECURSOR_DEFINITIONS_DIR`, or a
[workspace](#keeping-definitions-in-git) named by
`PRECURSOR_DEFINITIONS_WORKSPACE`. Every `*.agent.yaml` and
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
  refused with a `409`. So is an id carried by two files (a copy made without
  changing it).

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
- A removed prompt step's private agent is **kept**, with its history, in case
  the step comes back (a cut and paste, a pull then a revert); it takes the
  agent back by its key. Deleting the workflow in the app removes those for
  good.
- A step whose agent file can't be used — missing, moved, broken, or an id
  shared with another file — **blocks the run** (`409`) rather than being
  skipped, which for a gate would be a silent pass.
- **Never mid-run:** a running, paused or waiting workflow runs the file
  *version it started from* — its steps, settings and step prompts — whatever
  happens to the file meanwhile; the new version applies from the next run. The
  version is stored with the run (`definition_snapshot`), so a restart, a
  resume or a retry carries on with it too — and a typo made in the file
  meanwhile doesn't fail it. If the step rows were reshaped to a newer file
  after the run stopped, continuing it is refused: start a new run. Two
  exceptions, both made in the app: a **settings** edit saved during a run
  applies to that run (only the settings you changed; the run keeps its own
  steps), and a **step** edit is refused until the run stops.

### Lists and detail views

- **A file new to this instance** — written by hand, or brought from another
  machine — gets a row the next time the Agents list or the Workflows gallery
  is loaded, and from then on behaves like any other agent or workflow. A new
  agent file referenced by a workflow step gets its row as soon as that
  workflow is read.
- **Moving or renaming a file** keeps the same agent or workflow, with its run
  history: identity is the `id` inside the file, not the path.
- **Which file** declares an agent or a workflow is shown on its page (a badge
  with the path; red when the file has errors, amber when there is none), and
  in the API as `definition`: `{state: "file" | "invalid" | "none", path,
  message}`. Agent runs and workflow runs carry `definition_path` and
  `definition_hash`. In database mode `definition` is `null`.

### Editing in the app

In files mode, saving an agent or a workflow from the app writes its file —
settings and steps alike — and the change shows up everywhere at once.

- **The first save** of an agent or workflow that has no file yet creates one,
  so the folder fills up as things are edited. So does creating a workflow or
  an agent in the app.
- **Step keys are kept** across saves from the step editor (which rebuilds every
  step), so a prompt step keeps its private agent and history. A step the save
  can't recognise gets a new key derived from its label.
- **Deleting** an agent or workflow in the app deletes its file; left behind,
  the next list would bring it straight back. Archiving leaves the file alone.
- **A settings-only save** (name, policy, limits…) rewrites those settings and
  keeps the file's steps exactly as they are, including steps edited there
  that the app hasn't picked up yet.
- **A transfer import** writes the files of the agents and workflow it created
  or replaced.
- **A file with errors is never overwritten** from the app: the save is refused
  with a `409`, so a half-finished hand edit isn't lost. Fix the file first.

::: warning Comments are not kept
The app regenerates the whole file in the export's layout when it saves, so
comments and custom ordering in that file are lost (the header comment is
written back). Keeping them needs a comment-preserving YAML library
(`ruamel.yaml`), a new dependency that has to go through the lockfile workflow.
:::

### Permission changes need a human

A definition file decides what an agent may do. Once files can arrive from a
`git pull`, a teammate or a tool writing to disk, a change that **widens** that
must not take effect silently, so each agent and workflow keeps the permissions
a person last accepted. A file that goes beyond them can't start a run —
starting the agent fails with the list of changes, and running the workflow is
refused with a `409` — until someone accepts them in the app (**Review &
accept** on the badge, or `POST /api/definitions/accept`).

What counts:

| Agent | Workflow |
| --- | --- |
| a looser approval policy (anything but a move to `manual`, or down between two explicit policies) | a looser approval policy for every step |
| autonomy switched on, or more autonomous steps | a step added, or a step now running a different agent or its own prompt |
| MCP tools, skills or memory switched on | a step's MCP tools, skills or memory switched on |
| more MCP servers, or every server instead of a list | a step reaching more MCP servers |
| a higher token budget, or none | an approval step removed, or steps moved out from behind it |

- **Narrowing never needs review.**
- **What Precursor writes is accepted**: the export and every save from the app
  record the file's permissions, since those values came from you. That's also
  why a save that would **carry an unreviewed widening** along with your edit is
  refused — while one that undoes it (the way to reject a change from the app)
  goes through.
- **Accept applies to the version you reviewed**: the app sends that version's
  hash, and if the file changed since, the accept is refused.
- **A running agent** keeps the approval policy its run started with, and its
  autonomous loop stops for review when the file widens autonomy, its steps or
  its budget mid-run.
- **A file new to this instance** (adopted from disk) starts with nothing
  accepted, so everything it grants is listed once.
- **A row linked before this existed** is compared with its database columns —
  the values last set in the app. A workflow's steps can't be vouched for that
  way, so such a workflow is reviewed once.
- **The assistant's file tools** (`workspace-fs`, `drawio`) can't write
  anywhere under the definitions folder. The command runner can still write in
  a workspace it's given: a permission change it makes is caught by the review,
  but a rewritten prompt is not — don't give it the definitions workspace.

This gates **permissions, not content**: a prompt rewritten within the
permissions already accepted is not held.

## Keeping definitions in git

Set `PRECURSOR_DEFINITIONS_WORKSPACE` to a workspace's slug — optionally
followed by a folder inside it, `team-defs/precursor` — and that workspace's
working copy becomes the definitions folder. Clone a definitions repository as a
workspace, point Precursor at it, and:

- **Pull** from the Files section. The pulled files apply at once: new files get
  rows, workflows' steps follow their files, and anything that widens
  permissions waits for review as usual.
- **Edit** definition files in the Files section. Opening one shows the check's
  findings for it under the editor — errors and warnings with their location —
  refreshed on every save, and a save applies immediately.
- **Commit and push** from the Files section; saves made elsewhere in the app
  (agent settings, the step editor) land in the working copy the same way.
- **Check in CI** with `precursor validate <folder>` in the definitions
  repository itself.

The assistant's file tools still can't write there, even though it's a
workspace. `PRECURSOR_DEFINITIONS_DIR`, when set, takes precedence.

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
5. ✅ The Agents and Workflows lists come from the files: new files appear,
   moved files keep their history, and each page shows its file.
6. ✅ Editing in the app writes the file (comments are not kept yet — see
   above).
7. ✅ Permission changes arriving from disk (approval policy, MCP scope,
   autonomy, tools, budget, new steps) are held until a human accepts them; the
   assistant's file tools cannot write the definitions folder.
8. ✅ The definitions folder can be a git workspace: pull, check, accept, push.
9. The old declaration columns are removed from the database.
