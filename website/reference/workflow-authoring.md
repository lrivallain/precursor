---
title: Workflow authoring spec
description: A precise, machine-oriented specification of Precursor's workflow engine — for coding assistants that generate workflow definitions.
---

# Workflow authoring spec

This page is written for **coding AI agents** asked to produce a Precursor
workflow. It is a specification, not a tutorial: exact field names, exact
grammars, exact runtime semantics, and the constraints the server enforces.

For the prose introduction, read [Workflows](/features/workflows) instead. Where
this page and a feature page disagree, this one describes the implementation.

::: tip Give this page to your assistant
The whole spec is self-contained. Point an assistant at
`https://lrivallain.github.io/precursor/reference/workflow-authoring` (or paste
it) and it has everything needed to emit a valid workflow without reading the
source.
:::

## Mental model

A **workflow** is an ordered list of **steps**. Each step is a reference to an
**agent** (a reusable prompt-holder) plus per-step configuration. A **run** is
one execution of the list.

```
workflow ──┬── step 0  → agent A   ─┐
           ├── step 1  → agent B    │ the workflow owns the ordering,
           ├── step 2  (gate)  → C  │ the hand-off, and the lifecycle
           └── step 3  (approval)  ─┘
```

Three rules follow from this, and they are the ones authors get wrong:

1. **Agents do not know about each other.** An agent cannot call the next step.
   All sequencing is workflow-owned.
2. **A step is a reference, not a copy.** The same agent may back steps in
   several workflows. Anything you set *on the step* (instructions, capability
   overrides, tool scope) is snapshotted onto that execution and never written
   back to the shared agent row.
3. **Runs are unattended.** Every non-gate step is force-fed an autonomy
   directive forbidding clarifying questions. Design steps that can always
   produce *something*.

## Choose an authoring surface

| Surface | Use when | Entry point |
| --- | --- | --- |
| **YAML transfer document** | You are generating a workflow from scratch, or shipping one as a file. **Recommended.** | `POST /api/transfer/preview` then `POST /api/transfer/import` |
| **REST API** | You are editing a workflow that already exists on this install. | `POST` / `PATCH /api/workflows` |

The YAML document is the better target for a generator: it addresses agents
**positionally** within its own file, so it needs no knowledge of local database
ids, and it carries the agents it depends on. The REST payload requires real
`agent_id` values (or inline `task` text).

Both surfaces require **Agents mode** to be enabled, or every call returns
`409 Agents mode is disabled`.

## The YAML document

One object per file. A `kind: workflow` document carries the workflow *and*
every agent its steps reference.

```yaml
format: 1                 # TRANSFER_FORMAT_VERSION — refuse-on-mismatch
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

Import is deliberately two calls, because name collisions need a decision:

```http
POST /api/transfer/preview   { "content": "<the yaml>" }
→ TransferPreview { conflicts[], warnings[], agent_count, step_count }

POST /api/transfer/import    { "content": "<the yaml>", "resolutions": [...] }
→ TransferImportResult { workflow_id, created_agent_ids, ... }
```

Each conflict is keyed by `{kind, index}` and resolved with an `action`:

| Action | Meaning | Valid for |
| --- | --- | --- |
| `create` | Import as a new object alongside the existing one. | agents, workflow |
| `replace` | Overwrite the existing row. | agents, workflow |
| `link` | Reuse the existing row untouched; import nothing. | agents only |

Unresolved conflicts fall back to the preview's `default`, which is only
`replace` when `export_id` proves it is literally the same object. **A scripted
import that sends no resolutions is therefore never destructive.**

### What never travels

Webhook tokens (per-install credentials) and an *enabled* schedule. A carried
schedule always imports **disabled**, so a shared file cannot start firing on
its new owner.

## Field reference

### `workflow`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | **Required.** 1–200 chars. |
| `description` | string | `null` | |
| `icon` | string | `null` | ≤ 40 chars. |
| `color` | string | `null` | ≤ 24 chars. |
| `clear_artifacts` | bool | `true` | `true` wipes the artifact blackboard between runs. Set `false` only for a deliberately cumulative board. |
| `max_loops` | int | `3` | 1–25. Caps gate **and** approval-rework loop-backs. |
| `step_timeout_seconds` | int \| null | `null` | 30–86400. The stall watchdog. `null` (or `0` via `PATCH`) disables it. |
| `role` / `role_id` | — | `null` | Assistant role applied to every step's agent for the duration of the run. |
| `approval_policy` | enum \| null | `null` | Tool-approval policy for every step. `null` = each agent keeps its own. |
| `steps` | list | `[]` | Ordered. Position is the list index. |

### `steps[]`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `agent` (YAML) / `agent_id` (API) | int \| null | `null` | Agent index / database id. Must be `null` for `kind: approval`. |
| `task` (API only) | string | `null` | Authors an agent inline instead of referencing one. |
| `reusable` (API only) | bool | `false` | `false` → a private vessel that dies with the step. `true` → a real agent listed under **Agents**. |
| `name` | string | `null` | Step label. ≤ 200 chars. Falls back to the agent's title. |
| `kind` | `task` \| `inline` \| `gate` \| `approval` | `task` | See [step kinds](#step-kinds). |
| `instructions` | string | `null` | ≤ 8000 chars. Supports [placeholders](#placeholder-grammar). |
| `on_error` | `fail` \| `retry` \| `continue` | `fail` | |
| `max_retries` | int | `0` | 0–10. Only meaningful with `on_error: retry`. |
| `on_fail_position` | int \| null | `null` | 0-based loop-back target for a gate FAIL or an approval rework. |
| `on_reject` | `rework` \| `stop` \| `skip` | `rework` | Approval steps only. |
| `context_mode` | `auto` \| `selected` \| `none` | `auto` | See [context modes](#context-modes). |
| `context_sources` | string | `null` | Comma/semicolon-separated 0-based positions, e.g. `"0,2"`. ≤ 200 chars. |
| `use_mcp` | bool \| null | `null` | Tri-state. `null` inherits the agent's setting. |
| `use_skills` | bool \| null | `null` | Tri-state. |
| `use_memory` | bool \| null | `null` | Tri-state. |
| `mcp_servers` | string \| null | `null` | Server allowlist. See [tool scoping](#tool-scoping). |

### `agents[]`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `title` | string | — | **Required.** 1–200 chars. The collision key on import. |
| `task` | string | `""` | The agent's standing objective. |
| `model` | string \| null | `null` | Pinned model; a preview warning is emitted if unavailable locally. |
| `use_mcp` / `use_skills` / `use_memory` | bool | `true` | The agent's own baseline, which a step may override. |
| `max_steps` | int | `12` | 1–100. |
| `max_retries` | int | `0` | 0–10. |
| `token_budget` | int \| null | `null` | |
| `approval_policy` | enum \| null | `null` | |
| `inline` | bool | `false` | `true` marks it as a step's private vessel — excluded from conflict checks. |

## Step kinds

| Kind | Runs | Produces output? | Agent required? |
| --- | --- | --- | --- |
| `task` | a referenced, reusable agent | yes | yes |
| `inline` | a private vessel that dies with the step | yes | authored via `task` |
| `gate` | an agent that votes PASS/FAIL | **no** — transparent to the data flow | yes |
| `approval` | nobody; the run parks for a human | **no** — transparent | **must be `null`** |

"Transparent to the data flow" is load-bearing: a step following a gate or an
approval receives the last **producing** step's output (`task` / `inline`), not
the gate's verdict or the reviewer's note.

### Server-side normalisation

The server repairs rather than rejects, so know what it will silently do:

| Input | Result |
| --- | --- |
| `kind` not in the enum | coerced to `task` |
| `kind: approval` with an `agent_id` or `task` | agent forced to `null` |
| `on_error` / `on_reject` / `context_mode` not in their enum | coerced to the default |
| step with neither an agent nor a `task` | **`400`** — the one hard rejection |
| `agent_id` pointing at nothing | **`404 Agent {id} not found`** |
| `on_fail_position` out of range | accepted at write time; **resolved at runtime** to the previous runnable step |

There is no write-time validation that `on_fail_position` is a real, earlier,
runnable step. Get it right yourself.

## Placeholder grammar

Placeholders are substituted into a step's **`instructions` only** — never into
the agent's own `task`. The agent is handed resolved text and never sees a raw
template.

```
{{ <expr> }}
{{ <expr> | <fallback> }}
```

The matching regex is
`\{\{\s*(?P<expr>[A-Za-z0-9._-]+)\s*(?:\|(?P<default>[^}]*))?\}\}`.

| Expression | Resolves to |
| --- | --- |
| <code v-pre>{{run.input}}</code> | This run's brief. |
| <code v-pre>{{step.N.output}}</code> | The output recorded for 0-based position `N` **in this run**. |
| <code v-pre>{{state.&lt;key&gt;}}</code> | A value from [pipeline state](#pipeline-state). |

Resolution rules, exactly:

- The separator is a single `|`. The fallback runs to the closing brace, so it
  may contain spaces and punctuation but **not** `}`.
- The resolved value and the fallback are both `.strip()`ed.
- A value that is missing **or whitespace-only** falls through to the fallback.
- With no fallback, an unresolved placeholder renders as the literal string
  `(unset)` — an explicit absence, never a silent blank.
- An **unrecognised** expression is left untouched, so unrelated brace syntax in
  your prose survives intact.
- <code v-pre>{{step.N.output}}</code> reads the **run trace**, not live artifacts, so it still
  resolves after the blackboard is cleared. If a gate re-drove step `N`, it
  resolves to that step's **latest** attempt.

::: warning Always supply a fallback for state
On a pipeline's first run nothing is stored, so every <code v-pre>{{state.…}}</code> renders
`(unset)`. Write <code v-pre>{{state.cursor | the beginning of time}}</code> so the first run has
sane instructions.
:::

## What a step receives

The coordinator composes one kickoff preamble per step, joining present sections
with `\n\n---\n\n` in **exactly this order**:

1. **Run brief** — <code v-pre>{{run.input}}</code>, if the run was started with one.
2. **Reviewer directives** — notes from every approval already cleared in this
   run (a bare `Approved.` is excluded). This is how "translate it to French
   before sending" reaches a step two hops later.
3. **Unblock guidance** — the answer supplied when resuming a blocked step.
4. **Rejection feedback** — the note from an approval that sent this step back.
5. **Failure reason** — why the previous attempt failed, on a retry.
6. **Upstream hand-off** — the previous producer's output (per `context_mode`).
7. **Artifact board** — earlier steps' artifacts, oldest first.
8. **State key index** — key names, sizes and mtimes. **Never bodies.**
9. **Gate contract** *or* **autonomy directive** — see below.
10. **Step instructions** — placeholders resolved. Last, because closing lines
    carry the most weight.

Sections 1–5 are always delivered regardless of `context_mode`; that setting
governs the **material** (6–7), not the intent.

::: warning The state index is conditional
Section 8 appears only when the step can actually reach the built-in `precursor`
MCP server — i.e. `use_mcp` is not `false` **and** `mcp_servers` is either unset
or includes `precursor`. A step scoped to, say, `"fetch"` is told nothing about
state and has no `workflow_state_*` tools to read it with. Scope accordingly if
a step must read or write state.
:::

### The autonomy directive

Every `task` and `inline` step gets this appended verbatim. `gate` steps get the
gate contract instead; `approval` steps run no agent at all.

> You are an automated step in a workflow — it runs unattended, so there is no
> human available to answer questions mid-run. Act fully autonomously: carry out
> YOUR objective directly, treating the material above as your input. Do NOT ask
> for clarification, present a menu of options, or request confirmation, and
> never emit NEED_INPUT — if a detail is underspecified, pick the most
> reasonable interpretation and proceed anyway. Produce the actual deliverable
> your objective calls for (not a description of what you could do), publish it
> with an ARTIFACT directive, and end with 'OBJECTIVE_COMPLETE: \<2-3 sentence
> summary\>'.

Do not restate this in your instructions. Do write objectives that are
answerable without clarification — a step that genuinely cannot proceed parks
the whole run as `blocked`.

## Context modes

| Mode | The step receives |
| --- | --- |
| `auto` (default) | The nearest earlier **producing** step's output, plus every earlier producing step's artifacts. |
| `selected` | Only the positions named in `context_sources`. |
| `none` | No upstream material at all. |

`context_sources` parsing: `;` is normalised to `,`, chunks are trimmed,
non-numeric chunks are **discarded silently**, out-of-range and self-referencing
positions are dropped, duplicates are removed — and the survivors are **sorted
ascending**.

::: warning The hand-off is the highest position, not the last one you typed
Because the list is sorted, `context_sources: "3,1"` and `"1,3"` are identical:
position **3** becomes the immediate hand-off and position 1 becomes reference
material. Order your intent by position number, not by list order.
:::

If `selected` resolves to nothing (all chunks invalid), the step receives no
upstream material — the same as `none`.

The efficient pattern is `context_mode: none` plus an explicit
<code v-pre>{{step.N.output}}</code> placeholder: the step names the one thing it needs instead of
inheriting a whole transcript.

## Pipeline state

Named values scoped to the **workflow**, shared by all its steps, surviving
across runs. This is the only place a scheduled pipeline can record what it must
not redo.

| | Scope | Survives a run? |
| --- | --- | --- |
| Agent state | one agent, shared by every workflow using it | yes |
| Artifacts | one agent **run** | no |
| **Pipeline state** | **one workflow, all steps** | **yes** |

Read it with a <code v-pre>{{state.&lt;key&gt;}}</code> placeholder; write it with the
`workflow_state_set` MCP tool, which resolves the owning workflow per call.
`workflow_state_get`, `workflow_state_list` and `workflow_state_delete` complete
the set.

**Constraints, enforced:**

| Rule | Value |
| --- | --- |
| Key grammar | `^[a-z0-9][a-z0-9._-]*$` — lowercased and trimmed before validation |
| Key length | ≤ 120 chars |
| Value length | ≤ 100 000 chars |
| Keys per workflow | ≤ 200 (exceeding returns `409`) |

State is for **bookkeeping** — a cursor, a set of seen ids, a baseline. A
document belongs in an artifact; a large file belongs in a workspace with only
its path recorded here.

## Tool scoping

`mcp_servers` is a comma-separated allowlist, parsed by splitting on `,`,
trimming each part, and discarding empties. It is **tri-state**:

| Value | Effect |
| --- | --- |
| `null` (absent) | Every enabled server attaches. |
| `"fetch, workiq"` | Only those. Whitespace around names is trimmed. |
| `""` (empty or all-blank) | No servers at all — identical to `use_mcp: false`. |

This is a real allowlist enforced at attach time, not a request in the prompt: a
server you did not name is never attached, so the step **cannot** call it and its
schemas cost nothing. Names are matched exactly and are **never validated
against the local registry** — a workflow is portable, so an unknown name simply
matches nothing rather than failing the import or the run.

Two consequences worth planning for:

- The first-party `precursor` server is scoped like any other. Omitting it from a
  non-empty allowlist removes the `workflow_state_*` tools *and* suppresses the
  state key index.
- An allowlisted server that is installed and enabled but **signed out** is a
  hard requirement: the step parks `Blocked` naming the server rather than
  quietly answering without it.

Tool schemas are re-sent on every turn, so scoping is the single most effective
cost control in a long pipeline. Prefer naming the one or two servers a step
needs over leaving it on "all".

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

Verdict parsing is deliberately lenient, and **not** a strict prefix match. Both
patterns are case-insensitive word-boundary searches over the whole summary:

- **FAIL** matches `FAIL`, `FAILED`, `FAILURE`, `UNSAFE`, `REJECT`, `REJECTED`,
  `NO GO`, `NO-GO`, `NOGO`, `DENY`, `DENIED`, `NOT SAFE`, `NOT OK`,
  `NOT APPROVED`.
- **PASS** matches `PASS`, `PASSED`, `SAFE`, `APPROVE`, `APPROVED`, `APPROVES`,
  `ACCEPT`, `ACCEPTED`, `OK`, `GO`, `YES`.
- **FAIL wins ties**, and an empty or unparseable verdict is **fail-open**
  (treated as PASS) so a gate can never wedge a pipeline.

::: warning Keep gate reasons terse
Because FAIL wins and matching is a substring search over the whole summary, a
*passing* gate whose reason mentions "…no unsafe content…" trips the FAIL
pattern on the word `unsafe`. Instruct gates to give a short reason that avoids
the vocabulary of the opposite verdict.
:::

On FAIL the coordinator re-drives `on_fail_position`, injecting the gate's
critique so the retry knows what to fix. If `on_fail_position` is `null`,
out of range, or not runnable, it falls back to the previous runnable step; if
there is no earlier step at all, the run **fails**.

### Approvals

The run parks in `awaiting_approval` and spends nothing until a human decides.

| Decision | Effect |
| --- | --- |
| Approve | Resumes at the next step. The note is recorded on the trace **and forwarded to every later step** as a reviewer directive. |
| Reject → `rework` (default) | Loops back to `on_fail_position` (or the previous runnable step), with the note injected as the feedback to address. |
| Reject → `stop` | Ends the run as **`cancelled`**, not `failed` — nothing broke, a human decided. |
| Reject → `skip` | Abandons the rejected work and continues with the following step. |

`POST /api/workflows/{id}/reject` may override the step's declared `on_reject`
for a single decision via its `action` field.

### Failures and retries

| `on_error` | Behaviour |
| --- | --- |
| `fail` (default) | Stops the run. |
| `retry` | Re-drives the same step while `retry_count < max_retries`, injecting the failure reason so the retry is not a blind repeat. Exhausting the budget stops the run. |
| `continue` | Records the failure and advances. For steps whose output is a nice-to-have. |

Retry budgets are **per run**, so a scheduled pipeline does not exhaust its
allowance over its lifetime. A manual `POST /retry` resets the counter.

The **stall watchdog** (`step_timeout_seconds`) cancels a step running longer
than the limit and puts it through this same policy — so a timeout can retry, be
skipped, or stop the run like any other failure. Without it, a wedged agent
parks an unattended pipeline in `running` indefinitely.

### Loop budget

`max_loops` (default `3`, range 1–25) caps **both** gate loop-backs and approval
reworks, counted per step via its `attempt_count`. Exceeding it puts the workflow
in `failed` rather than looping forever.

## Statuses

| Object | Non-terminal | Terminal |
| --- | --- | --- |
| Workflow | `draft`, `idle`, `running`, `paused`, `awaiting_approval` | `completed`, `failed`, `cancelled` |
| Run | `running`, `paused`, `awaiting_approval` | `completed`, `failed`, `cancelled` |

A newly created workflow is `draft`/`idle` and runs only when triggered.

A run also records what started it — `trigger` is one of `manual`, `schedule`,
`webhook` or `resume` (anything else is coerced to `manual`).

## REST surface

| Method & path | Purpose |
| --- | --- |
| `GET /api/workflows` | List (`?includeArchived`) |
| `POST /api/workflows` | Create with steps |
| `GET /api/workflows/{id}` | Fetch one |
| `PATCH /api/workflows/{id}` | Update fields; a non-null `steps` **replaces the entire list** |
| `DELETE /api/workflows/{id}` | Delete |
| `POST /api/workflows/{id}/run` | Start, optional `{ "input": "…" }` run brief |
| `POST /api/workflows/{id}/pause` \| `/resume` \| `/cancel` | Lifecycle. `resume` takes an optional `input` answering what blocked the step |
| `POST /api/workflows/{id}/retry` | Re-drive one step (`{ "position": N, "input": "…" }`) |
| `POST /api/workflows/{id}/approve` \| `/reject` | Clear or bounce an approval (`{ "note": "…", "action": "rework\|stop\|skip" }`) |
| `POST /api/workflows/{id}/permission` | Answer a tool-permission gate |
| `GET /api/workflows/{id}/runs` | Persisted run traces (`?limit`) |
| `GET`/`PUT`/`DELETE /api/workflows/{id}/state` | Pipeline state |
| `PUT /api/workflows/{id}/schedule` | Recurrence |
| `POST`/`DELETE /api/workflows/{id}/webhook` | Mint / revoke a token |
| `POST /api/workflows/hooks/{token}` | Trigger; the posted body becomes the run brief |
| `GET /api/transfer/workflows/{id}` | Export as YAML |

Errors an authoring client must handle:

| Status | Condition |
| --- | --- |
| `400` | A step has neither an agent nor an inline `task` |
| `404` | Unknown workflow, agent, step attempt, state key or webhook token |
| `409` | Agents mode disabled; editing steps while `running`; replaying mid-flight; state key cap exceeded |

Lifecycle changes broadcast a `workflow.changed`
[SSE event](/reference/api#real-time-events).

## Authoring checklist

Before emitting a workflow, verify:

- [ ] Every step has an agent, an inline `task`, or is `kind: approval`.
- [ ] `approval` steps carry **no** agent.
- [ ] Every `on_fail_position` names a real, earlier, runnable step.
- [ ] Every <code v-pre>{{state.…}}</code> has a `| fallback` for the first run.
- [ ] <code v-pre>{{step.N.output}}</code> references only positions **before** this step.
- [ ] Any step that reads or writes state leaves `mcp_servers` unset or includes
      `precursor`.
- [ ] Gate objectives demand a terse reason that avoids opposite-verdict words.
- [ ] Steps that only transform text set `use_mcp: false` or a narrow
      `mcp_servers`, and `use_memory: false`.
- [ ] Long pipelines use `context_mode: none` + placeholders rather than
      inheriting every transcript.
- [ ] Anything irreversible (sending, publishing, filing) sits behind an
      `approval` step.
- [ ] A scheduled pipeline sets `step_timeout_seconds` so a wedged step cannot
      park it forever.

## Worked example

A stateful digest — read a cursor, work relative to it, write the new cursor —
is the shape most scheduled pipelines take. The repo ships it at
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

Note the three moves that make it stateful: a cursor read with a **safe
first-run default**, work performed relative to it, and the new cursor written
at the end for the *next* run to read.

## See also

- [Workflows overview](/features/workflows) — the prose introduction.
- [Configuring a step](/features/workflows/steps) — the same concepts, explained.
- [Import & export](/features/transfer) — how documents travel between installs.
- [MCP](/features/mcp) — what a tool server is and how it attaches.
- [API reference](/reference/api) — the wider HTTP surface and SSE events.
