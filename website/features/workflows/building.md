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

Hit **Edit steps** on the workflow view and the strip you already know becomes
editable — same horizontal pipeline, same shape, now authorable:

- **Drag a card** left or right to reorder — the whole card is grabbable, and a
  vertical seam shows exactly where it will land. A click without movement still
  opens the card.
- **Click a card** to open that step's own settings modal. Pick its kind first —
  **Agent**, **Inline**, **Gate** or **Approval** — then, for an Agent step,
  [where its agent comes from](#where-a-step-s-agent-comes-from): an **existing
  agent** from the Agents section, or a **new agent** created right here. Then set its
  [instructions](/features/workflows/steps#per-step-instructions),
  [what it's fed](/features/workflows/steps#what-each-step-is-fed),
  [what it may use](/features/workflows/steps#what-each-step-may-use) and its
  [failure policy](/features/workflows/running#when-a-step-fails-retry-carry-on-or-stop).
- **The + between cards** inserts a step at that position — hovering the gap
  draws the seam the new card will be spliced into. The one at the end appends,
  and the bin on a card removes it.

Keeping the horizontal layout is deliberate: the strip is the picture of the
pipeline, and it shouldn't change shape just because you're editing it.

Editing is an explicit mode with an explicit **Save steps**, because saving
**replaces** the whole step list server-side. For the same reason it's refused
while a run is in flight — rewriting the steps mid-run would strand the
coordinator's cursor. Stop the run first.

You can also edit a step's agent from the step modal outside edit mode: its
objective and capability toggles are editable in place, so tuning a prompt
doesn't mean leaving the workflow.

Because steps are just agents, anything else you can do to an agent — its
artifacts, schedule, triggers — is still done in agents mode. The workflow only
owns the *sequence*.

Since agents are shared, the reverse link matters too. Every agent **card on the
agents board** carries a workflow chip with the number of pipelines using it.
Click it and, if there's only one, you land straight in that workflow; if there
are several, the card names them so you can pick. The agent's settings panel
keeps the same count and list. Editing an agent that three pipelines depend on
should not be a surprise.

## Where a step's agent comes from

A step that runs an agent has to get one from somewhere, and the choice is
really about **where that agent lives afterwards**:

| Source | What it creates | Where it lives |
| --- | --- | --- |
| **Existing agent** | nothing — it references one | already in **Agents** |
| **New agent** | a reusable agent | joins **Agents** on save, outlives the step |
| **Inline** step / **Inline prompt** on a gate | a private vessel | nowhere you manage — dies with the step |

**New agent** exists so building a pipeline doesn't mean breaking off to create
its parts first: name it, give it its objective, save the steps, and it's a
normal agent — editable in Agents, pickable by other steps and other workflows.
Once saved it *is* an existing agent, so reopening the step shows it as a plain
reference; creating is a one-time act, not a mode the step stays in.

::: tip Sharing one agent across pipelines
**Existing agent** is safe to reuse from several workflows — even ones running
at the same time. Each step opens its own
[agent run](/features/agents-mode#an-agent-is-a-definition-each-start-is-a-run), so
concurrent pipelines don't share a status, a transcript, a blackboard, a set of
tool grants or a token meter. The only thing they share is the *definition*: edit
the agent and both pick the change up on their next run.

Each board reads **its own** step's run, so two pipelines sharing an agent show
their own status, result and question — not whichever one finished last. On the
agent's side, the [Runs rail](/features/agents-mode#an-agent-is-a-definition-each-start-is-a-run)
filters the transcript down to a single execution.
:::

An **Agent** step only offers those first two, because writing a one-off prompt
there would be an Inline step under another name. A **Gate** offers all three:
there's no "inline gate" kind, so a one-off check has to be authored in the step.

## Inline steps: one-off work that isn't an agent

Not every step deserves a reusable agent. Whenever you write a step's prompt
**in the step** — an Inline step, or a Gate whose check you type there — that
prompt belongs to the step alone:

- Nothing is added to your **Agents** list — the pipeline doesn't leave a trail
  of single-purpose agents behind it.
- It is **removed with the step**, so deleting the step (or the workflow) cleans
  up after itself.
- It behaves exactly like a Task step in the run: it produces content, hands it
  downstream, and can be gated, retried or given its own context and capability
  settings.

Under the hood a step still needs *something* to execute it — the runtime is
agent-keyed — so an inline step keeps a private, hidden agent as its vessel. You
never manage it: editing the step edits the vessel in place (so its run history
survives), and removing the step deletes it. The one place it still surfaces is
the **needs-attention** list, deliberately: if an inline step blocks on a tool
approval it has to stay discoverable, or a workflow could wedge invisibly.

A vessel is only ever cleaned up when **no saved step points at it any more**.
That matters if you edit a pipeline over the [API](/features/workflows/reference)
rather than in the builder: a step that names its vessel with `agent_id` keeps
it, whether or not the save resends the prompt — so changing one policy field is
safe and never costs you the step's history.

A gate is often the clearest case for it: "is *this specific joke* safe for
kids?" is rarely worth a permanent agent.

## Gates and loop-back

A step can be flagged as a **gate** instead of a plain task. A gate is a
quality check that votes **PASS** or **FAIL** on the work so far, and on failure
sends the run **back to an earlier step** to try again — enabling chains like:

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
  critique injected as a preamble so the retry knows *what* to fix. The run then
  marches forward and naturally re-reaches the gate.
- The verdict is **fail-open**: an empty or ambiguous answer counts as PASS, so a
  gate never wedges the pipeline.

A gate is **transparent to the data flow**: it judges the work but doesn't
replace it. The step *after* a gate receives the last real producer's output —
the material the gate validated — not the gate's terse `PASS: …` verdict. So in
*tell a joke → is it kid-safe? → note the joke*, the final step sees the joke
itself, exactly as if the gate weren't there.

Because a gate is a **judge, not a producer**, its own output stays out of the
deliverables: the raw `OBJECTIVE_COMPLETE: PASS: …` control line is never shown
as the step's result, and the gate leaves **no artifact** on the shared board.
The step just displays a plain `Passed — <reason>` / `Rejected — <reason>`
verdict. More broadly, control directives (`OBJECTIVE_COMPLETE`, `ARTIFACT`,
`PROGRESS`, …) are scrubbed from every step's *displayed* result — the raw turn
is still kept internally for parsing and hand-off, but the plumbing never leaks
into the work you read.

Each loop-back bumps the gate's **attempt counter** (shown as a badge on the
step). A workflow-level **max loops** cap (default **3**, range 1–25) bounds the
retries: once a gate fails more times than the cap, the workflow stops in the
`failed` state instead of looping forever. In the builder, a gate exposes an
**on fail → step N** target (defaults to the previous runnable step) and the
workflow carries the **max loops** value.

## Human approval checkpoints

A [gate](#gates-and-loop-back) is an *agent* judging the work. An **approval**
step is a **human** judging it. The run **parks** on it — no agent runs, nothing
is spent while it waits — until you decide. Put one in front of anything
irreversible: sending the email, publishing the post, filing the ticket.

While parked, the workflow reads `Needs you` and the detail board shows a
decision panel with the checkpoint's brief and a note box. You can:

- **Approve & continue** — the pipeline resumes at the next step. Your note is
  recorded on the trace **and forwarded to every later step** as a reviewer
  directive. That's the escape hatch for a mid-run course correction: approve the
  Italian joke but add *"translate it into French before sending"* and the
  sending step obeys, without editing the workflow. The note rides *alongside*
  the content — an approval publishes nothing itself, so the material the
  reviewer saw is still what flows downstream.
- **Reject** — what happens next is the checkpoint's **reject policy**:

| Policy | On reject |
| --- | --- |
| **Send back** (default) | Loops back to an earlier step to be redone, with your note injected as the feedback to address — the same machinery (and `max loops` cap) a failing gate uses. |
| **Stop the run** | Ends the run there. Recorded as `cancelled`, not `failed`: nothing broke, you decided. This is the "don't do this at all" answer a checkpoint in front of an irreversible action exists for. |
| **Skip ahead** | Abandons the rejected work and carries on with the following step. |

The policy is set per checkpoint in the builder, but it isn't a cage — the
decision panel always also offers **Stop the run**, so a reviewer can end a run
outright no matter how the checkpoint was configured.

An approval step has **no agent**, is transparent to the data flow (like a gate,
it forwards the last real producer's output rather than anything of its own), and
appears in the run trace as its own violet `Approval` row.

## Defaults for a new workflow

**Settings → Workflows** holds the defaults a fresh workflow and its steps begin
from: whether a new step may use **tools**, **skills** and **memory**, the
[**tool-approval policy**](/features/workflows/running#tool-approvals-for-the-whole-pipeline),
and the **stall watchdog** a new workflow carries. Every one stays overridable per
workflow and per step — this only decides the starting point, so a fleet that
mostly transforms text can default tools off rather than switching each step by
hand.

One subtlety worth knowing: a default of *on* leaves the step's override unset
so it simply inherits its agent, while a default of *off* is written onto the
step explicitly — "inherit" would otherwise quietly turn it back on.
