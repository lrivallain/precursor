---
title: Building a pipeline
---

# Building a pipeline

How you author a workflow: creating it, editing its steps on the board, and
choosing what each step *is*. For what a step is then given and allowed to use,
see [configuring a step](/features/workflows/steps).

## Creating a workflow

Open **Workflows** in the sidebar and hit **New workflow**. The dialog names the
workflow and sets its run-wide options (icon, artifact reset, gate retry cap,
stall watchdog, Assistant role). It deliberately does **not** contain the steps —
a brand-new workflow starts empty and you author its steps on the board itself.

A workflow is created in `draft`/`idle` and doesn't run until something
[triggers](/features/workflows/running#triggers-and-scheduling) it.

## Steps are edited on the board

Hit **Edit steps** and the strip you already know becomes editable — same
horizontal pipeline, now authorable:

- **Drag a card** left or right to reorder. A click without movement still opens
  the card.
- **Click a card** to open that step's settings modal. Pick its kind first —
  **Agent**, **Inline**, **Gate** or **Approval** — then, for an Agent step,
  [where its agent comes from](#where-a-step-s-agent-comes-from). Then set its
  [instructions](/features/workflows/steps#per-step-instructions),
  [what it's fed](/features/workflows/steps#what-each-step-is-fed),
  [what it may use](/features/workflows/steps#what-each-step-may-use) and its
  [failure policy](/features/workflows/running#when-a-step-fails-retry-carry-on-or-stop).
- **The + between cards** inserts a step at that position; the one at the end
  appends, and the bin on a card removes it.

Editing is an explicit mode with an explicit **Save steps**, because saving
**replaces** the whole step list server-side. For the same reason it's refused
while a run is in flight — rewriting the steps mid-run would strand the
coordinator's cursor. Stop the run first.

Because steps are just agents, anything else you can do to an agent — its
artifacts, schedule, triggers — is still done in agents mode. The workflow only
owns the *sequence*.

Since agents are shared, the reverse link matters too. Every agent card on the
agents board carries a workflow chip with the number of pipelines using it, so
editing an agent that three pipelines depend on isn't a surprise.

## Where a step's agent comes from

A step that runs an agent has to get one from somewhere, and the choice is really
about **where that agent lives afterwards**:

| Source | What it creates | Where it lives |
| --- | --- | --- |
| **Existing agent** | nothing — it references one | already in **Agents** |
| **New agent** | a reusable agent | joins **Agents** on save, outlives the step |
| **Inline** step / **Inline prompt** on a gate | a private vessel | nowhere you manage — dies with the step |

**New agent** exists so building a pipeline doesn't mean breaking off to create
its parts first: name it, give it its objective, save the steps, and it's a
normal agent — editable in Agents, pickable by other steps and workflows.

::: tip Sharing one agent across pipelines
**Existing agent** is safe to reuse from several workflows — even ones running at
the same time. Each step opens its own
[agent run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run), so
concurrent pipelines don't share a status, a transcript, a blackboard or a token
meter. The only thing they share is the *definition*: edit the agent and both
pick the change up on their next run.
:::

An **Agent** step only offers the first two, because writing a one-off prompt
there would be an Inline step under another name. A **Gate** offers all three:
there's no "inline gate" kind, so a one-off check has to be authored in the step.

## Inline steps: one-off work that isn't an agent

Not every step deserves a reusable agent. Whenever you write a step's prompt **in
the step** — an Inline step, or a Gate whose check you type there — that prompt
belongs to the step alone:

- Nothing is added to your **Agents** list — the pipeline doesn't leave a trail
  of single-purpose agents behind it.
- It is **removed with the step**, so deleting the step (or the workflow) cleans
  up after itself.
- It behaves exactly like a Task step in the run: it produces content, hands it
  downstream, and can be gated, retried or given its own settings.

Under the hood a step still needs *something* to execute it, so an inline step
keeps a private, hidden agent as its vessel. You never manage it. The one place
it still surfaces is the **needs-attention** list, deliberately: if an inline
step blocks on a tool approval it has to stay discoverable, or a workflow could
wedge invisibly.

A gate is often the clearest case for it: "is *this specific joke* safe for
kids?" is rarely worth a permanent agent.

## Gates and loop-back

A step can be flagged as a **gate** instead of a plain task. A gate is a quality
check that votes **PASS** or **FAIL** on the work so far, and on failure sends
the run **back to an earlier step** to try again — enabling chains like:

1. **Task** — *tell me a story*
2. **Gate** — *ensure the story is safe for a kid; otherwise rerun step 1*
3. **Task** — *note the provided story*

You don't need special prompting. When a step is a gate, the coordinator appends
a short instruction telling the agent to end its turn with a verdict:

```
OBJECTIVE_COMPLETE: PASS: <reason>
OBJECTIVE_COMPLETE: FAIL: <reason>
```

- **PASS** → the workflow advances to the next step as usual.
- **FAIL** → the gate's **on-fail target** step is re-driven, with the gate's
  critique injected as a preamble so the retry knows *what* to fix.
- The verdict is **fail-open**: an empty or ambiguous answer counts as PASS, so a
  gate never wedges the pipeline.

A gate is **transparent to the data flow**: it judges the work but doesn't
replace it. The step *after* a gate receives the last real producer's output —
the material the gate validated — not the gate's terse verdict. Because a gate is
a judge rather than a producer, it leaves **no artifact** on the shared board and
the step just displays `Passed — <reason>` / `Rejected — <reason>`.

Each loop-back bumps the gate's **attempt counter**. A workflow-level **max
loops** cap (default **3**, range 1–25) bounds the retries: once a gate fails
more times than the cap, the workflow stops in the `failed` state instead of
looping forever.

## Human approval checkpoints

A [gate](#gates-and-loop-back) is an *agent* judging the work. An **approval**
step is a **human** judging it. The run **parks** on it — no agent runs, nothing
is spent while it waits — until you decide. Put one in front of anything
irreversible: sending the email, publishing the post, filing the ticket.

While parked, the workflow reads `Needs you` and the board shows a decision panel
with the checkpoint's brief and a note box. You can:

- **Approve & continue** — the pipeline resumes at the next step. Your note is
  recorded on the trace **and forwarded to every later step** as a reviewer
  directive. That's the escape hatch for a mid-run course correction: approve the
  Italian joke but add *"translate it into French before sending"* and the sending
  step obeys, without editing the workflow.
- **Reject** — what happens next is the checkpoint's **reject policy**:

| Policy | On reject |
| --- | --- |
| **Send back** (default) | Loops back to an earlier step to be redone, with your note injected as the feedback to address. |
| **Stop the run** | Ends the run there. Recorded as `cancelled`, not `failed`: nothing broke, you decided. |
| **Skip ahead** | Abandons the rejected work and carries on with the following step. |

The policy is set per checkpoint, but it isn't a cage — the decision panel always
also offers **Stop the run**.

An approval step has **no agent**, is transparent to the data flow, and appears
in the run trace as its own violet `Approval` row.

## Defaults for a new workflow

**Settings → Workflows** holds the defaults a fresh workflow and its steps begin
from: whether a new step may use **tools**, **skills** and **memory**, the
[**tool-approval policy**](/features/workflows/running#tool-approvals-for-the-whole-pipeline),
and the **stall watchdog**. Every one stays overridable per workflow and per
step — this only decides the starting point, so a fleet that mostly transforms
text can default tools off rather than switching each step by hand.
