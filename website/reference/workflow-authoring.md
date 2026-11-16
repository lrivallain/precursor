---
title: Workflow authoring spec
description: A precise, machine-oriented specification of Precursor's workflow engine — for coding assistants that generate workflow definitions.
---

# Workflow authoring spec

A specification for **coding AI agents** asked to produce a Precursor workflow:
exact field names, grammars, runtime semantics and server-enforced constraints.
It is self-contained; point an assistant at
`https://lrivallain.github.io/precursor/reference/workflow-authoring` or paste it.
Where this page and a feature page ([Workflows](/features/workflows)) disagree,
this one describes the implementation.

## Mental model

A **workflow** is an ordered list of **steps**. Each step references an
**agent** (a reusable prompt holder) plus per-step configuration. A **run** is
one execution of the list.

```
workflow ──┬── step 0  → agent A   ─┐
           ├── step 1  → agent B    │ the workflow owns the ordering,
           ├── step 2  (gate)  → C  │ the hand-off, and the lifecycle
           └── step 3  (approval)  ─┘
```

1. **Agents don't know about each other.** All sequencing is workflow-owned.
2. **A step is a reference, not a copy.** The same agent may back steps in
   several workflows. Step settings (instructions, capability overrides, tool
   scope) apply to that execution only and are never written to the agent.
3. **Runs are unattended.** Every `task`/`inline` step gets an
   [autonomy directive](#the-autonomy-directive) forbidding clarifying
   questions. Design steps that can always produce *something*.

## Choose an authoring surface

| Surface | Use when | Entry point |
| --- | --- | --- |
| **YAML transfer document** | Generating a workflow from scratch, or shipping one as a file. **Recommended.** | `POST /api/transfer/preview`, then `POST /api/transfer/import` |
| **REST API** | Editing a workflow that already exists on this install. | `POST` / `PATCH /api/workflows` |
| **Definition files** | The install runs in files mode and you commit `*.workflow.yaml` / `*.agent.yaml` directly. Different format (steps addressed by `key`). | See [Definition files](/features/definitions) |

The transfer document addresses agents **by index** within the file, needs no
local database ids, and carries the agents it depends on. The REST payload needs
real `agent_id` values or inline `task` text. In files mode, REST and import
writes are saved to the definition files.

Creating or importing a workflow, and every run action (run, resume, retry,
replay, approve/reject, permission, webhook trigger), require **Agents mode**;
otherwise they return `409 Agents mode is disabled`.

## The YAML document

One object per file. A `kind: workflow` document carries the workflow and every
agent its steps reference.

```yaml
format: 1                 # transfer format version; other versions are refused
kind: workflow
exported_by: my-generator # free-form provenance, optional

agents:                   # steps reference these BY 0-BASED INDEX
  - title: Researcher
    task: >-
      Gather the facts on the subject you are given and report them as a
      compact, sourced list.
    use_mcp: true
    use_skills: false
    use_memory: false

  - title: Writer
    task: Turn a set of findings into a short, publishable brief.

workflow:
  name: Research and brief
  description: Two-stage research pipeline.
  icon: sparkles
  clear_artifacts: true
  max_loops: 3
  steps:
    - agent: 0            # index into `agents:` above
      name: Research
      kind: task
      instructions: |
        Research this subject: {{run.input | the current state of the project}}

    - agent: 1
      name: Write the brief
      kind: task
      context_mode: none
      instructions: |
        Write a brief from these findings:

        {{step.0.output | (the previous step produced nothing — say so)}}
```

### Import protocol

Two calls, because name collisions need a decision:

```http
POST /api/transfer/preview   { "content": "<the yaml>" }
→ TransferPreview { conflicts[], warnings[], agent_count, step_count }

POST /api/transfer/import    { "content": "<the yaml>", "resolutions": [...] }
→ TransferImportResult { workflow_id, created_agent_ids, ... }
```

Each conflict is keyed by `{kind, index}` (`kind`: `agent` | `workflow`) and
resolved with an `action`:

| Action | Meaning | Valid for |
| --- | --- | --- |
| `create` | Import as a new object alongside the existing one. | agents, workflow |
| `replace` | Overwrite the existing row. | agents, workflow |
| `link` | Reuse the existing row untouched; import nothing. | agents only |

Unresolved conflicts use the preview's `default`, which is `replace` only when
`export_id` proves it is the same object. **An import with no resolutions is
never destructive.** Roles are matched by name and linked by default. Agents
with `inline: true` are never conflict-checked.

**Never carried:** webhook tokens. A carried `schedule` always imports
**disabled**.

## Field reference

### `workflow`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | **Required.** 1–200 chars. |
| `description` | string | `null` | |
| `icon` | string | `null` | ≤ 40 chars. |
| `color` | string | `null` | ≤ 24 chars. |
| `clear_artifacts` | bool | `true` | `true` wipes the artifact board between runs. `false` only for a deliberately cumulative board. |
| `max_loops` | int | `3` | 1–25. Caps gate **and** approval-rework loop-backs. |
| `step_timeout_seconds` | int \| null | `null` | 30–86400. The stall watchdog. REST create without it uses the **Settings → Workflows** default; `PATCH` with `0` disables it. |
| `role` | YAML: `{name, system_prompt}`; REST: role name | `null` | Assistant role applied to every step's agent during the run. REST returns `404` for an unknown name; `""` in `PATCH` clears it. |
| `approval_policy` | `manual` \| `balanced` \| `autonomous` \| null | `null` | **REST only** (not in the YAML document). Tool-approval policy for every step; `null` = each agent keeps its own. |
| `schedule` | object | `null` | YAML only; see [`schedule`](#schedule). REST uses `PUT /schedule`. |
| `steps` | list | `[]` | Ordered; position = list index. A workflow with no steps is `draft`. |

### `steps[]`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `agent` (YAML) / `agent_id` (REST) | int \| null | `null` | Agent index / database id. Ignored for `kind: approval`. |
| `task` (REST only) | string | `null` | Authors the step's agent inline instead of referencing one. |
| `title`, `model` (REST only) | string | `null` | Title (≤ 200) and model of an agent authored from `task`. |
| `reusable` (REST only) | bool | `false` | With `task`: `false` → a private vessel deleted with the step; `true` → a real agent listed under **Agents**. |
| `name` | string | `null` | Step label, ≤ 200 chars. Falls back to the agent's title. |
| `kind` | `task` \| `inline` \| `gate` \| `approval` | `task` | See [step kinds](#step-kinds). |
| `instructions` | string | `null` | ≤ 8000 chars. Supports [placeholders](#placeholder-grammar). |
| `on_error` | `fail` \| `retry` \| `continue` | `fail` | See [failures](#failures-and-retries). |
| `max_retries` | int | `0` | 0–10. Used with `on_error: retry`. |
| `on_fail_position` | int \| null | `null` | 0-based loop-back target for a gate FAIL or an approval rework. |
| `on_reject` | `rework` \| `stop` \| `skip` | `rework` | Approval steps only. |
| `context_mode` | `auto` \| `selected` \| `none` | `auto` | See [context modes](#context-modes). |
| `context_sources` | string | `null` | Positions for `selected`, e.g. `"0,2"`. ≤ 200 chars. |
| `use_mcp` / `use_skills` / `use_memory` | bool \| null | `null` | `null` inherits the agent's setting. On REST writes, an omitted value takes the **Settings → Workflows** default (written as `false` when that default is off). |
| `mcp_servers` | string \| null | `null` | Server allowlist, ≤ 400 chars. See [tool scoping](#tool-scoping). |

### `agents[]` (YAML)

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `title` | string | — | **Required.** 1–200 chars. The collision key on import. |
| `task` | string | `""` | The agent's standing objective. |
| `model` | string \| null | `null` | Pinned model; the preview warns if it's unavailable locally. |
| `autonomy_enabled` | bool | `false` | Run as an [autonomous mission](/features/agents-mode/missions). |
| `use_mcp` / `use_skills` / `use_memory` | bool | `true` | The agent's baseline; steps may override. |
| `max_steps` | int | `12` | 1–100. Mission step budget. |
| `max_retries` | int | `0` | 0–10. |
| `token_budget` | int \| null | `null` | ≥ 1; `null` = unlimited. |
| `approval_policy` | `manual` \| `balanced` \| `autonomous` \| null | `null` | `null` inherits the global default. |
| `role` | `{name, system_prompt}` \| null | `null` | |
| `schedule` | object \| null | `null` | See [`schedule`](#schedule). |
| `export_id` | string \| null | `null` | Portable identity, set by export. Omit when generating. |
| `inline` | bool | `false` | `true` marks a step's private vessel (excluded from conflict checks). |

### `schedule`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `interval_seconds` | int \| null | `null` | ≥ 60. |
| `run_at_minute` | int \| null | `null` | 0–1439, minute of day in `timezone`; overrides the interval. |
| `timezone` | string | `UTC` | |
| `days_of_week` | int | `127` | 7-bit mask, bit 0 = Monday … bit 6 = Sunday. |
| `extra_rules` | list | `[]` | Additional rules with the same fields. |
| `clear_context` | bool | `true` | Agent schedules only: fresh transcript per run. |

## Step kinds

| Kind | Runs | Produces output? | Agent |
| --- | --- | --- | --- |
| `task` | a referenced, reusable agent | yes | required |
| `inline` | a private vessel deleted with the step | yes | authored via `task` (REST) or an `inline: true` agent (YAML) |
| `gate` | an agent that votes PASS/FAIL | **no** (transparent) | required (referenced or inline) |
| `approval` | nobody; the run parks for a human | **no** (transparent) | none |

A step after a gate or approval receives the last **producing** step's
(`task`/`inline`) output, not the verdict or the reviewer's note.

### Server-side normalisation

The server repairs rather than rejects:

| Input | Result |
| --- | --- |
| `kind` not in the enum | coerced to `task` |
| `kind: approval` with an agent or `task` | agent dropped |
| `on_error` / `on_reject` / `context_mode` not in their enum | coerced to the default |
| step with neither an agent nor a `task` | **`400`**, the one hard rejection |
| `agent_id` pointing at nothing | **`404 Agent {id} not found`** |
| `on_fail_position` out of range | accepted; **resolved at runtime** to the previous runnable step |

Nothing validates at write time that `on_fail_position` is a real, earlier,
runnable step. Get it right yourself.

## Placeholder grammar

Substituted into a step's **`instructions` only**, never the agent's `task`. The
agent only sees resolved text.

```
{{ <expr> }}
{{ <expr> | <fallback> }}
```

Regex: `\{\{\s*(?P<expr>[A-Za-z0-9._-]+)\s*(?:\|(?P<default>[^}]*))?\}\}`

| Expression | Resolves to |
| --- | --- |
| <code v-pre>{{run.input}}</code> | This run's brief. |
| <code v-pre>{{step.N.output}}</code> | The output of 0-based position `N` **in this run**. |
| <code v-pre>{{step.&lt;key&gt;.output}}</code> | Same, by step key. **Definition files only** (key grammar `[a-z0-9][a-z0-9_-]*`); elsewhere left untouched. |
| <code v-pre>{{state.&lt;key&gt;}}</code> | A value from [pipeline state](#pipeline-state). |

- The fallback runs to the closing brace: spaces and punctuation are fine, `}`
  is not.
- Value and fallback are both stripped. A missing **or whitespace-only** value
  uses the fallback.
- With no fallback, an unresolved placeholder renders as `(unset)`.
- An **unrecognised** expression is left untouched.
- <code v-pre>{{step.N.output}}</code> reads the **run trace**, not live
  artifacts, so it survives a cleared board; for a re-run step it is the
  **latest** attempt. It is capped at **8000 characters**; a longer value is
  cut and ends with `… [truncated: <n> characters, capped at 8000]`. Move large
  payloads through artifacts and `context_mode` instead.

::: warning Always supply a fallback for state
On the first run nothing is stored, so every <code v-pre>{{state.…}}</code>
renders `(unset)`. Write <code v-pre>{{state.cursor | the beginning of time}}</code>.
:::

## What a step receives

The kickoff preamble joins the present sections with `\n\n---\n\n` in **this
order**:

1. **Run brief**: <code v-pre>{{run.input}}</code>, if the run has one.
2. **Reviewer directives**: notes from every approval already cleared in this
   run (a bare `Approved.` is excluded).
3. **Unblock guidance**: the answer given when resuming a blocked step.
4. **Rejection feedback**: the note from an approval that sent this step back.
5. **Failure reason**: why the previous attempt failed, on a retry.
6. **Upstream hand-off**: the previous producer's output (per `context_mode`).
7. **Artifact board**: earlier steps' artifacts, oldest first.
8. **State key index**: key names, sizes and mtimes. **Never bodies.**
9. **Gate contract** *or* **autonomy directive** (below).
10. **Step instructions**, placeholders resolved. Last, so they carry the most
    weight.

Sections 1–5 are delivered regardless of `context_mode`, which governs only 6–7.

::: warning The state index is conditional
Section 8 appears only when the step can reach the built-in `precursor` MCP
server: `use_mcp` is not `false` **and** `mcp_servers` is unset or includes
`precursor`. Otherwise the step has neither the index nor the
`workflow_state_*` tools.
:::

### The autonomy directive

Appended verbatim to every `task` and `inline` step (`gate` steps get the gate
contract; `approval` steps run nothing):

> You are an automated step in a workflow — it runs unattended, so there is no
> human available to answer questions mid-run. Act fully autonomously: carry out
> YOUR objective directly, treating the material above as your input. Do NOT ask
> for clarification, present a menu of options, or request confirmation, and
> never emit NEED_INPUT — if a detail is underspecified, pick the most
> reasonable interpretation and proceed anyway. Produce the actual deliverable
> your objective calls for (not a description of what you could do), publish it
> as an artifact — a line 'ARTIFACT: \<title\>', the full deliverable on the
> lines below it, then a line 'END_ARTIFACT' — and end with
> 'OBJECTIVE_COMPLETE: \<2-3 sentence summary\>'.

Don't restate it. Write objectives answerable without clarification; a step
that genuinely can't proceed parks the run as blocked.

Every step's `PROGRESS:` and `ARTIFACT:` lines are read whether or not its agent
is autonomous. The hand-off forwards each published artifact once, as a board
entry, and drops its copy from the message text. `OBJECTIVE_COMPLETE:` and
`NEED_INPUT:` only steer an autonomous agent; a non-autonomous step finishes
when its turn ends. A turn that ends with **no output and no token spend** (a
dropped stream) counts as a failure.

## Context modes

| Mode | The step receives |
| --- | --- |
| `auto` (default) | The nearest earlier **producing** step's output, plus every earlier producing step's artifacts. |
| `selected` | Only the positions in `context_sources`. The **highest** position is the hand-off; the others are reference material. |
| `none` | No upstream material. |

`context_sources` parsing: `;` becomes `,`; chunks are trimmed; non-numeric,
out-of-range and self-referencing chunks are dropped silently; duplicates are
removed; the result is **sorted ascending**. So `"3,1"` equals `"1,3"`. If
nothing survives, the step gets no upstream material (same as `none`).

The hand-off is always **this run's**: if the source step produced nothing in
this run, the step is told "produced no output in this run". Write objectives
that can report that gap. With `clear_artifacts: false`, the **artifact board**
spans runs; the hand-off stays run-scoped.

Efficient pattern: `context_mode: none` plus an explicit
<code v-pre>{{step.N.output}}</code>.

## Pipeline state

Named values scoped to the **workflow**, shared by its steps, kept across runs.
The only place a scheduled pipeline can record what it must not redo.

| | Scope | Survives a run? |
| --- | --- | --- |
| Agent state | one agent, shared by every workflow using it | yes |
| Artifacts | one agent **run** | no |
| **Pipeline state** | **one workflow, all steps** | **yes** |

Read with <code v-pre>{{state.&lt;key&gt;}}</code>; write with the
`workflow_state_set` MCP tool. `workflow_state_get`, `workflow_state_list` and
`workflow_state_delete` complete the set. `workflow_id` defaults to the workflow
running the calling agent, resolved per call.

| Rule | Value |
| --- | --- |
| Key grammar | `^[a-z0-9][a-z0-9._-]*$`, lowercased and trimmed first |
| Key length | ≤ 120 chars |
| Value length | ≤ 100,000 chars |
| Keys per workflow | ≤ 200 (a new key past that returns `409`; overwriting is allowed) |

Store cursors, seen ids, baselines. Documents go in artifacts; large files in a
workspace, with only the path stored here.

## Tool scoping

`mcp_servers` is a comma-separated allowlist (split on `,`, trimmed, empties and
duplicates dropped):

| Value | Effect |
| --- | --- |
| `null` (absent) | Every enabled server attaches. |
| `"fetch, workiq"` | Only those. |
| `""` (empty or all-blank) | No servers; same as `use_mcp: false`. |

Enforced at attach time: an unnamed server is never attached, so the step
**cannot** call it and its schemas cost nothing. Names match exactly and are
**not validated** against the local registry; an unknown name matches nothing.

- `precursor` is scoped like any other server. Leaving it out of a non-empty
  list removes the `workflow_state_*` tools **and** the state key index.
- An allowlisted server that is installed and enabled but **signed out** parks
  the step `Blocked`, naming the server.

Tool schemas are re-sent every turn: name the one or two servers a step needs.

## Control flow

### Gates

A `gate` step's agent receives this contract verbatim:

> You are a QUALITY GATE in an automated workflow. Judge the material from the
> previous step (shown above) against your objective. Do NOT rewrite it or
> produce new content — only decide.
>
> End your turn with EXACTLY ONE final line, and nothing after it:
> `OBJECTIVE_COMPLETE: PASS: <one short reason>` — if it meets the objective
> `OBJECTIVE_COMPLETE: FAIL: <what must change>` — if it does not

Verdict parsing is lenient: case-insensitive, word-boundary searches over the
whole completion summary (not a prefix match).

- **FAIL** words: `FAIL`, `FAILED`, `FAILURE`, `UNSAFE`, `REJECT`, `REJECTED`,
  `NO GO`, `NO-GO`, `NOGO`, `DENY`, `DENIED`, `NOT SAFE`, `NOT OK`,
  `NOT APPROVED`.
- **PASS** words: `PASS`, `PASSED`, `SAFE`, `APPROVE`, `APPROVED`, `APPROVES`,
  `ACCEPT`, `ACCEPTED`, `OK`, `GO`, `YES`.
- **FAIL wins.** Empty or unparseable is **PASS** (fail-open).

::: warning Keep gate reasons terse
Because FAIL wins anywhere in the summary, a passing reason like "…no unsafe
content…" fails on `unsafe`. Instruct gates to give a short reason that avoids
the opposite verdict's words.
:::

On FAIL, `on_fail_position` is re-run with the gate's critique injected. If it
is `null`, out of range or not runnable, the previous runnable step is used; if
there is none, the run **fails**. The new attempt's output replaces the old one
downstream. A gate publishes no artifact.

### Approvals

The run parks in `awaiting_approval` and spends nothing until a human decides.

| Decision | Effect |
| --- | --- |
| Approve | Resume at the next step. The note is recorded **and forwarded to every later step** as a reviewer directive. |
| Reject → `rework` (default) | Loop back to `on_fail_position` (or the previous runnable step) with the note as feedback. |
| Reject → `stop` | End the run as **`cancelled`**, not `failed`. |
| Reject → `skip` | Drop the rejected work and continue with the next step. |

`POST /reject` may override the step's `on_reject` for one decision via `action`.

### Failures and retries

| `on_error` | Behaviour |
| --- | --- |
| `fail` (default) | Stop the run. |
| `retry` | Re-run the step while `retry_count < max_retries`, injecting the failure reason; then stop the run. |
| `continue` | Record the failure and advance. |

Retry budgets are **per run**; a manual `POST /retry` resets the counter.

The **stall watchdog** (`step_timeout_seconds`) cancels a step running longer
than the limit and applies the same policy. Without it, a wedged agent keeps an
unattended run `running` indefinitely.

### Loop budget

`max_loops` (default `3`, 1–25) caps both gate loop-backs and approval reworks,
counted per step (`attempt_count`). Exceeding it fails the run.

## Statuses

| Object | Non-terminal | Terminal |
| --- | --- | --- |
| Workflow | `draft`, `idle`, `running`, `paused`, `awaiting_approval` | `completed`, `failed`, `cancelled` |
| Run | `running`, `paused`, `awaiting_approval` | `completed`, `failed`, `cancelled` |

A new workflow is `draft` with no steps, `idle` with steps, and runs only when
triggered. A run's `trigger` is `manual`, `schedule`, `webhook` or `resume`
(anything else is stored as `manual`).

## REST surface

| Method & path | Purpose |
| --- | --- |
| `GET /api/workflows` | List (`?include_archived=true` to include archived) |
| `GET /api/workflows/archived` | List archived only |
| `POST /api/workflows` | Create with steps |
| `GET /api/workflows/{id}` | Fetch one |
| `PATCH /api/workflows/{id}` | Update fields; a non-null `steps` **replaces the whole list** (`409` while running) |
| `DELETE /api/workflows/{id}` | Delete (and its inline vessels; reusable agents are kept) |
| `POST /api/workflows/{id}/run` | Start; optional `{ "input": "…" }` run brief |
| `POST /api/workflows/{id}/pause` \| `/cancel` | Lifecycle |
| `POST /api/workflows/{id}/resume` | Resume; optional `{ "input": "…" }` answering what blocked the step |
| `POST /api/workflows/{id}/retry` | Re-run one step of a stopped run: `{ "position": N, "input": "…" }`, both optional (default: the step that failed) |
| `POST /api/workflows/{id}/permission` | Answer a tool-permission request: `{ "request_id": "…", "decision": "approve-once" \| "approve-always" \| "deny" }` |
| `POST /api/workflows/{id}/approve` \| `/reject` | Clear or bounce an approval: `{ "note": "…" (≤ 4000), "action": "rework" \| "stop" \| "skip" }` (`action` on reject only) |
| `GET /api/workflows/{id}/runs` | Run traces with per-step attempts, newest first (`?limit`, default 20, max 100) |
| `GET /api/workflows/{id}/run-steps/{stepRunId}/events` | One attempt's agent activity |
| `POST /api/workflows/{id}/run-steps/{stepRunId}/replay` | Re-run one attempt on its recorded input, advancing nothing (`409` while the run is in flight) |
| `POST /api/workflows/{id}/archive` \| `/unarchive` | Archive toggle |
| `PUT /api/workflows/{id}/schedule` | Recurrence: flat fields or a `rules` array for [several cadences](/features/scheduler#several-cadences-on-one-item) |
| `GET /api/workflows/{id}/state` | List pipeline state |
| `PUT /api/workflows/{id}/state` | Upsert `{ "key": "…", "value": "…" }` |
| `DELETE /api/workflows/{id}/state/{key}` \| `/state` | Delete one key, or all |
| `POST` \| `DELETE /api/workflows/{id}/webhook` | Mint / revoke a webhook token |
| `POST /api/workflows/hooks/{token}` | Trigger (`202`); the posted body becomes the run brief |
| `GET /api/transfer/workflows/{id}` | Export as YAML |

| Status | Condition |
| --- | --- |
| `400` | A step has neither an agent nor an inline `task` |
| `404` | Unknown workflow, agent, role, step attempt, state key or webhook token |
| `409` | Agents mode disabled; editing steps while running; replaying mid-flight; state key cap; a run/resume the engine refuses |

Lifecycle changes broadcast a `workflow.changed`
[SSE event](/reference/api#real-time-events). Every workflow read includes
`run_progress` (see [Workflows reference](/features/workflows/reference#api-surface)).

## Authoring checklist

- [ ] Every step has an agent, an inline `task`, or is `kind: approval`.
- [ ] `approval` steps carry **no** agent.
- [ ] Every `on_fail_position` names a real, earlier, runnable step.
- [ ] Every <code v-pre>{{state.…}}</code> has a `| fallback`.
- [ ] <code v-pre>{{step.N.output}}</code> only references positions **before** this step, and carries short values (≤ 8000 chars).
- [ ] Steps that read or write state leave `mcp_servers` unset or include `precursor`.
- [ ] Gates demand a terse reason that avoids opposite-verdict words.
- [ ] Text-only steps set `use_mcp: false` (or a narrow `mcp_servers`) and `use_memory: false`.
- [ ] Long pipelines use `context_mode: none` + placeholders instead of inheriting everything.
- [ ] Anything irreversible (sending, publishing, filing) sits behind an `approval` step.
- [ ] Scheduled pipelines set `step_timeout_seconds`.

## Worked example

A stateful digest: read a cursor with a safe first-run default, work relative
to it, write the new cursor for the next run. Shipped at
[`examples/workflows/stateful-digest.yaml`](https://github.com/lrivallain/precursor/blob/main/examples/workflows/stateful-digest.yaml).

```yaml
format: 1
kind: workflow
agents:
  - title: Surveyor
    task: Survey a subject and report findings as a compact, factual list.
  - title: Digest writer
    task: Turn findings into a short written digest.
  - title: State recorder
    task: Record the durable facts a pipeline needs on its next run.

workflow:
  name: Stateful digest
  clear_artifacts: true
  max_loops: 3
  steps:
    - agent: 0
      name: Survey since last run
      kind: task
      instructions: |
        Survey this subject: {{run.input | the state of this project}}

        The previous run finished at:
        {{state.last_digest_at | the beginning of time — this is the first run}}

        Report only what changed since then. Publish it as an artifact
        titled "Findings".

    - agent: 1
      name: Write the digest
      kind: task
      context_mode: none        # the findings arrive via the placeholder
      instructions: |
        Write a digest of these findings:

        {{step.0.output | (the previous step produced nothing — say so)}}

        Audience: {{state.audience | a general technical audience}}
        Five bullets at most. Publish it as an artifact titled "Digest".

    - agent: 2
      name: Record the cursor
      kind: task
      context_mode: none
      instructions: |
        Call `workflow_state_set` with key `last_digest_at` and the current
        UTC timestamp in ISO-8601. Do not store the digest itself — that is
        this run's deliverable and is already an artifact.
```

## See also

- [Workflows](/features/workflows) and [Configuring a step](/features/workflows/steps)
- [Definition files](/features/definitions)
- [Import & export](/features/transfer)
- [MCP](/features/mcp)
- [API reference](/reference/api)
