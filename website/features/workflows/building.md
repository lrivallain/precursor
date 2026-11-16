---
title: Building a pipeline
---

# Building a pipeline

Create a workflow, lay out its steps, and pick what each step is. For what a
step is given and may use, see [configuring a step](/features/workflows/steps).

## Creating a workflow

**Workflows → New workflow** sets the name and run-wide options: icon, artifact
reset between runs, gate loop cap, stall watchdog, tool approvals, Assistant
role. Steps are added afterwards on the board. A workflow is `draft` until it
has steps, then `idle` until something
[triggers](/features/workflows/running#triggers-and-scheduling) it.

## Steps are edited on the board

Click **Edit steps**:

- **Drag** a card to reorder; **click** it to open its settings (kind first,
  then [agent source](#where-a-step-s-agent-comes-from),
  [instructions](/features/workflows/steps#per-step-instructions),
  [context](/features/workflows/steps#what-each-step-is-fed),
  [capabilities](/features/workflows/steps#what-each-step-may-use) and
  [failure policy](/features/workflows/running#when-a-step-fails-retry-carry-on-or-stop)).
- **+** between cards inserts a step; the bin removes one.
- **Save steps** replaces the whole step list, so it's refused while a run is in
  flight. Stop the run first.

The agent itself (artifacts, schedule, triggers) is still edited in Agents. Each
agent card shows a chip with the number of workflows using it.

## Where a step's agent comes from

| Source | Creates | Lives in |
| --- | --- | --- |
| **Existing agent** | nothing (a reference) | **Agents** |
| **New agent** | a reusable agent | **Agents**, from save onward |
| **Inline** step / **Inline prompt** on a gate | a private vessel | nowhere you manage; deleted with the step |

Agent steps offer the first two; gates offer all three.

::: tip Sharing one agent across pipelines
Reusing an existing agent in several workflows, even concurrently, is safe:
each step opens its own
[agent run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)
with its own status, transcript, artifacts and tokens. Only the definition is
shared, so an edit applies to every workflow's next run.
:::

## Inline steps: one-off work that isn't an agent

A prompt written in the step (an Inline step, or a gate's inline prompt)
belongs to the step: nothing is added to **Agents**, and it's deleted with the
step or workflow. It otherwise behaves like an Agent step (produces output, can
be gated or retried). Its hidden agent does appear in the needs-attention list
if it blocks on a tool approval, and its approvals can be answered
[on the board](/features/workflows/running#tool-approvals-for-the-whole-pipeline).

## Gates and loop-back

A **gate** votes on the work so far and, on failure, sends the run back to an
earlier step:

1. **Task**: *tell me a story*
2. **Gate**: *the story must be safe for a kid; otherwise rerun step 1*
3. **Task**: *score the story*

The coordinator tells the gate to end with:

```text
OBJECTIVE_COMPLETE: PASS: <reason>
OBJECTIVE_COMPLETE: FAIL: <reason>
```

- **PASS**: the run advances.
- **FAIL**: the gate's **on-fail target** re-runs with the critique injected.
  The new attempt's output replaces the old one downstream.
- An empty or ambiguous verdict counts as **PASS**, so a gate never wedges a
  run. Exact parsing rules are in the
  [authoring spec](/reference/workflow-authoring#gates).

A gate publishes no artifact; it shows `Passed — <reason>` or
`Rejected — <reason>`. The workflow's **max loops** (default `3`, 1–25) caps
loop-backs; past it the run fails.

## Human approval checkpoints

An **approval** step parks the run (no agent, no spend) until you decide. The
workflow shows `Needs you` and the board shows a decision panel with the step's
instructions as the brief and a note box.

- **Approve & continue**: the run resumes. Your note is recorded and passed to
  **every later step** as a reviewer directive (e.g. approve, adding
  *"translate it into French before sending"*).
- **Reject**: applies the step's reject policy.

| Reject policy | Effect |
| --- | --- |
| **Send back** (default) | Loop back to an earlier step with your note as feedback |
| **Stop the run** | End the run as `cancelled` (not `failed`) |
| **Skip ahead** | Drop the rejected work and continue with the next step |

The panel always offers **Stop the run** as well. Approvals appear as violet
`Approval` rows in the run trace. Rework loops count against **max loops**.

## Defaults for a new workflow

**Settings → Workflows** sets the starting values for new workflows and steps:
whether steps may use **tools**, **skills** and **memory**, the
[tool-approval policy](/features/workflows/running#tool-approvals-for-the-whole-pipeline),
and the **stall watchdog**. All are overridable per workflow and per step.
