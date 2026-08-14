---
title: Workflows
---

# Workflows

**Workflows mode** chains independent [agents](/features/agents) into a
reusable, named pipeline that runs in the **background** — `research → draft →
review` — where the *workflow* owns the sequencing, not the agents. Each step is
just an existing agent (or one you create as you build), so agents stay reusable
on their own while the workflow coordinates the hand-off, lifecycle, and schedule.
Like agents, it's **opt-in and off by default**.

::: warning Work in progress
Workflows are a new orchestration layer built on top of agents mode. The linear
`step → step` pipeline is solid, but the surface is still evolving — expect the
UI and controls to keep changing.
:::

## How it differs from agent dependencies

Agents can already declare `depends-on` links that render a
[workflow strip](/features/agents). Workflows are the **coordinator-owned**
alternative:

- **Reusable, not baked in.** A workflow references agents by id. The same agent
  can appear in several workflows; the chaining lives on the workflow, so you
  reorder or reuse steps without touching the agents themselves.
- **One coordinator.** The workflow drives the run — it starts the first step,
  waits for it to rest, feeds its output to the next, and advances down the line.
  Agents don't trigger each other.
- **Implicit hand-off.** Each step receives the immediately preceding step's
  **full answer** plus its artifacts as a kickoff preamble, mirroring a pipeline
  stage. You don't have to prompt a step to "use the previous result" — the
  coordinator forwards it. No dependency graph to reason about.
- **Shared blackboard.** Beyond that immediate hand-off, a step also inherits the
  **artifacts published by every earlier step** in the run, labelled by step and
  oldest-first. A reviewer three stages down still sees the research inventory the
  first step produced — without the middle steps having to re-forward it. Steps
  that published nothing are skipped, and gates (which leave no deliverable)
  never appear on the board.

## Building a workflow

Open **Workflows** in the sidebar and hit **New workflow**. The dialog names the
workflow and sets its run-wide options (icon, artifact reset, gate retry cap,
stall watchdog, Assistant role). It deliberately does **not** contain the steps —
a brand-new workflow starts empty and you author its steps on the board itself.

### Steps are edited on the board

Hit **Edit steps** on the workflow view and the strip you already know becomes
editable — same horizontal pipeline, same shape, now authorable:

- **Drag a card** left or right to reorder — the whole card is grabbable, and a
  vertical seam shows exactly where it will land. A click without movement still
  opens the card.
- **Click a card** to open that step's own settings modal. Pick its kind first —
  **Agent**, **Inline**, **Gate** or **Approval** — then, for an Agent step,
  [where its agent comes from](#where-a-step-s-agent-comes-from): an **existing
  agent** from the Agents section, or a **new agent** created right here. Then set its
  [instructions](#per-step-instructions), [what it's fed](#what-each-step-is-fed),
  [what it may use](#what-each-step-may-use) and its
  [failure policy](#when-a-step-fails-retry-carry-on-or-stop).
- **The + between cards** inserts a step at that position — hovering the gap
  draws the seam the new card will be spliced into. The one at the end appends,
  and the bin on a card removes it.

Keeping the horizontal layout is deliberate: the strip is the picture of the
pipeline, and it shouldn't change shape just because you're editing it.

Editing is an explicit mode with an explicit **Save steps**, because saving
**replaces** the whole step list server-side. For the same reason it's refused
while a run is in flight — rewriting the steps mid-run would strand the
coordinator's cursor. Stop the run first.

You can also edit a step's agent from the [step modal](#running-and-monitoring)
outside edit mode: its objective and capability toggles are editable in place, so
tuning a prompt doesn't mean leaving the workflow.

Because steps are just agents, anything else you can do to an agent — its
artifacts, schedule, triggers — is still done in agents mode. The workflow only
owns the *sequence*.

Since agents are shared, the reverse link matters too. Every agent **card on the
agents board** carries a workflow chip with the number of pipelines using it.
Click it and, if there's only one, you land straight in that workflow; if there
are several, the card names them so you can pick. The agent's settings panel
keeps the same count and list. Editing an agent that three pipelines depend on
should not be a surprise.

### Where a step's agent comes from

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

An **Agent** step only offers those first two, because writing a one-off prompt
there would be an Inline step under another name. A **Gate** offers all three:
there's no "inline gate" kind, so a one-off check has to be authored in the step.

### Inline steps: one-off work that isn't an agent

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

A gate is often the clearest case for it: "is *this specific joke* safe for
kids?" is rarely worth a permanent agent.

### Where a new workflow starts

**Settings → Workflows** holds the defaults a fresh workflow and its steps begin
from: whether a new step may use **tools**, **skills** and **memory**, and the
**stall watchdog** a new workflow carries. Every one stays overridable per
workflow and per step — this only decides the starting point, so a fleet that
mostly transforms text can default tools off rather than switching each step by
hand.

One subtlety worth knowing: a default of *on* leaves the step's override unset
so it simply inherits its agent, while a default of *off* is written onto the
step explicitly — "inherit" would otherwise quietly turn it back on.

## Steps run autonomously — they never stop to ask

A workflow runs **unattended**: once you start it, there is no human sitting in
the loop to answer a mid-run question. So every task step is launched with a
strict autonomy directive — it must carry out its objective **directly on the
input it was handed**, and it is explicitly forbidden from asking for
clarification, presenting a menu of options, or emitting `NEED_INPUT`. If a
detail is under-specified, the step picks the most reasonable interpretation and
produces the deliverable anyway.

This keeps bare chains flowing without careful prompting. A step like *note this
joke from 0 to 10* just scores the joke it received, rather than stopping to ask
"what did you mean by *note*?" — which would otherwise park the step **Blocked**
and pause the entire run. (Gates are exempt: they follow their own PASS/FAIL
contract described below.)

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

## Per-step instructions

A step is a *reference* to an agent, and the same agent can appear in many
workflows. **Step instructions** are the extra mandate for that stage only,
layered on top of the agent's standing objective and taking precedence where they
differ. They appear **only on a step that reuses an existing agent** — that's the
one case where they add to a prompt written elsewhere. An Inline step, or an
Agent step whose agent is authored on the spot, already states its job in its own
field, and a second box would ask the same question twice:

> **Agent objective:** "Summarise the material you're given."
> **Step instructions:** "Three bullets, exec tone, lead with the number."

That's what makes an agent genuinely reusable — one `Summariser` row can be the
terse-bullets step in one pipeline and the long-form brief in another, with no
cloning. Instructions land at the *end* of the kickoff preamble, where they carry
the most weight. On an approval step, the instructions are what the reviewer sees
on the decision panel.

### Placeholders

Step instructions aren't static text — they can pull in live values, so one
generic definition adapts to each run and each step takes only what it needs:

| Placeholder | Resolves to |
| --- | --- |
| <code v-pre>{{run.input}}</code> | The [run brief](#the-run-brief-one-workflow-a-different-subject-each-run) for *this* run |
| <code v-pre>{{step.N.output}}</code> | What the step at 0-based position `N` produced this run |
| <code v-pre>{{state.&lt;key&gt;}}</code> | A value from the workflow's [saved state](#pipeline-state-what-a-workflow-remembers) |

Each takes an optional fallback after a pipe — <code v-pre>{{state.cursor | the
beginning of time}}</code> — which is what makes a first run safe, since nothing
is stored yet. Without one, an unresolved placeholder renders as `(unset)`: an
explicit absence the agent can reason about, rather than a silent blank that
quietly changes what the instruction says. Anything we don't recognise
(<code v-pre>{{mustache.thing}}</code>) is left untouched, so prose and other
tooling's braces survive.

Substitution happens **before** the agent is handed its instructions — it never
sees a raw template. <code v-pre>{{step.N.output}}</code> reads the run trace
rather than the agents' live artifacts, so it still resolves after the blackboard
is cleared, and a step re-driven by a [gate loop-back](#gates-and-loop-back)
resolves to its latest attempt.

::: tip Narrowing what a step is fed
<code v-pre>{{step.N.output}}</code> pairs well with `context_mode: none`:
instead of inheriting the whole upstream transcript, a step names the one earlier
output it needs. In a long pipeline that's the difference between a focused
prompt and an expensive, distracting one.
:::

## Pipeline state: what a workflow remembers

The run brief, the run trace and the artifact blackboard all describe **one
execution** — the blackboard is even wiped between runs. So a scheduled pipeline
had nowhere to record what it must not redo next time. **Pipeline state** is that
place: named values scoped to the workflow, shared by every step, kept **across
runs**.

<!-- The example ships in the repo so this isn't just prose — see
     examples/workflows/stateful-digest.yaml -->

It is deliberately *not* the same as an agent's
[own state](/features/agents#durable-state-the-private-scratchpad):

| | Scope | Survives a run? |
| --- | --- | --- |
| Agent state | One agent — which several pipelines may share | Yes |
| Artifacts | One agent, one run | **No** — cleared per run |
| **Pipeline state** | **One workflow, all its steps** | **Yes** |

That distinction is the whole point. A step points at a *reusable* agent, so a
cursor written under the agent's own scope is shared with every other pipeline
using that agent — and an inline agent's scratchpad dies with its step. A fact
like "the last invoice we processed" belongs to the **pipeline**.

Steps use it two ways:

- **Read** — a <code v-pre>{{state.&lt;key&gt;}}</code> placeholder in the step's
  instructions, resolved before the agent runs. The step is handed the value, not
  a lookup task.
- **Write** — the `workflow_state_set` tool, which defaults to whichever workflow
  is running the calling agent right now (resolved per call, since a shared agent
  belongs to no single pipeline). `workflow_state_get`, `workflow_state_list` and
  `workflow_state_delete` round it out.

Each step's kickoff also carries a **key index** — the names of the stored values,
never the bodies — so an agent knows what it can look up without paying for the
whole store in every prompt.

The **Pipeline state** panel on the workflow page lists what's saved, expands a
value, and lets you add, delete, or reset entries. Seeding a value by hand is how
you give a pipeline its starting cursor without faking a run; **reset** is the
lever when a saved cursor has gone bad and every run is working from a wrong
baseline.

### A worked example

The repo ships one at
[`examples/workflows/stateful-digest.yaml`](https://github.com/lrivallain/precursor/blob/main/examples/workflows/stateful-digest.yaml)
— import it from **Workflows → Import**. It's a three-step digest that shows the
whole loop:

1. **Survey since last run** reads <code v-pre>{{state.last_digest_at | the
   beginning of time…}}</code>. First run: nothing is stored, so the default
   lands and the step surveys everything.
2. **Write the digest** reads <code v-pre>{{step.0.output}}</code> plus
   <code v-pre>{{state.audience | a general technical audience}}</code> — an
   operator-tunable knob you seed from the panel without editing the workflow.
3. **Record the cursor** calls `workflow_state_set` to store the new
   `last_digest_at`, which is what step 1 reads on the *next* run.

Those three moves — read a cursor with a safe first-run default, work relative to
it, write the new cursor at the end — cover most stateful pipelines.

::: warning Bookkeeping, not a blob store
Values are capped (100 KB each, 200 keys per workflow) and are meant to be small
facts: a cursor, a set of seen ids, a baseline. A document belongs in an
**artifact**; a large file belongs in a [workspace](/features/workspaces), with
only its path recorded here.
:::

## When a step fails: retry, carry on, or stop

By default any failed step stops the run. Each step now carries its own **failure
policy**:

- **Stop run** (default) — today's behaviour, the conservative choice.
- **Retry** — re-drive the same step up to *N* times (1–10), with the failure
  reason injected so the retry isn't a blind repeat. Each attempt appends its own
  trace row.
- **Carry on** — record the failure and move to the next step. For steps whose
  output is a nice-to-have: an optional enrichment, a notification.

Retry budgets are **per run**, so a scheduled pipeline doesn't exhaust its
allowance over its lifetime.

### The stall watchdog

An agent that never returns would otherwise park an unattended pipeline in
`running` forever. Set a **stall watchdog** on the workflow (in minutes; `0` =
off, the default) and any step running longer than that is declared stuck: the
coordinator cancels its agent and puts it through the *same* failure policy
above — so a timeout can retry, be skipped, or stop the run exactly like any
other failure. The trace records it as a failure with a `watchdog` note.

## Cost: what the run actually spent

Every step attempt records its **token spend** — the delta across that turn — and
the run rolls them up. The run header shows the total, and each trace row shows
its own, so a gate that looped four times reveals what each pass cost. It turns
"did it work?" into "was it worth it?", and makes an expensive loop obvious.

## What each step is fed

By default a step inherits the **previous producer's output plus the accumulated
artifact board** — the implicit hand-off that makes a bare chain work with no
wiring. In a long pipeline that gets expensive and unfocused, so each step can
choose:

| Context | The step receives |
| --- | --- |
| **Previous step** (default) | The last real producer's output + every earlier step's artifacts. |
| **Pick steps** | Only the earlier steps you name (by step number). The last one named is the hand-off; the rest form its reference board. |
| **None** | Nothing upstream — the step runs on its own objective and the run brief alone. |

The run brief, reviewer directives and the step's own instructions are *always*
delivered; this setting governs the **material**, not the intent.

## What each step may use

A step can also narrow what its agent draws on. Each toggle is tri-state —
**auto** (inherit the agent's own setting), **on**, or **off**:

- **Tools** — MCP servers. Tool schemas are a large *fixed* context cost paid on
  every turn, so a step that only has to rewrite a paragraph shouldn't carry the
  whole catalogue. Off means no tool servers at all.
- **Skills** — stored skills. Off tells the agent to solve the task directly.
  (Skills are files the SDK discovers, so this is a directive, not a sandbox.)
- **Memory** — long-term memory. A pure transform step is usually better off not
  consulting it.

Because these change what's baked into the session, flipping one rebuilds the
agent's session on its next run. The agent itself carries the same three toggles
as its baseline (editable from the step modal); the step overrides them for the
duration of its turn.

### Picking which tool servers a step gets

**Tools: on** still means *every* enabled [MCP server](./mcp.md), and that is
rarely what a step needs. A modest install can register a few hundred tools
between them, and their schemas are re-sent on every turn — enough, in a
measured six-step briefing run, for the tool-using turns to account for well
over 95% of the run's tokens while each step actually needed exactly one server.

So a step with tools on can also name **which servers** it may see. The
**Servers** row in the step modal lists every server you've enabled in
**Settings → MCP**, with its tool count, so the cost is visible where the choice
is made. Enable a server there first — until you do, the row has only
Precursor's own server to offer, and says so.

| Selection | The step gets |
| --- | --- |
| **All** (default) | Every enabled server — the behaviour before this existed. |
| One or more servers | Only those. |
| Nothing selected | No tool servers at all — identical to **Tools: off**. |

Precursor's own first-party server is listed alongside the rest and is scoped
like any of them. It doesn't need enabling in **Settings → MCP** — it attaches
whenever a step has tools on — but it is one of the larger catalogues on a
normal install, so a step that only needs `fetch` shouldn't be paying for topic,
memory and schedule tools it will never call. Leave it selected if the step
writes back into Precursor (posting to a topic, storing a memory, setting a
reminder); a step's result is handed to the next step from the transcript either
way, so dropping it doesn't break the pipeline.

This is a real allowlist, not a request: the servers you didn't pick are never
attached to the session, so the step *cannot* call them and their schemas cost
nothing. Asking a model in the prompt not to use a tool is not equivalent —
it reliably reaches for one anyway.

The allowlist is per step. Changing it rebuilds that step's session on the next
run, so a shared agent moving from a `fetch`-only step to a `workiq`-only one
gets the right catalogue each time rather than reusing the previous one. A name
this machine can't attach shows as a struck-through chip and simply matches
nothing — **red** if nothing by that name is installed here, **amber** if it is
installed but switched off in **Settings → MCP**, which is the case you can fix
without leaving the app.

The name is kept rather than dropped, so an exported workflow imports cleanly
onto a machine with a different server set, and survives the trip back. Import
carries the allowlist verbatim, and the preview warns before you commit to it,
naming the servers this install can't attach and which of the two reasons
applies — so a step doesn't silently run with fewer tools than its author gave
it.

## Tool approvals for the whole pipeline

A step's agent can stop mid-run to ask permission for a tool call — and until
someone answers, the **entire run is parked**. For a workflow fired by a schedule
or a webhook there is nobody there to answer, so it stalls until the
[watchdog](#the-stall-watchdog) kills it.

**Tool approvals** in the workflow's settings sets the policy for every step's
agent while the pipeline runs — the same **Manual / Balanced / Autonomous**
choice an individual agent has. Like the [role](#one-voice-for-the-whole-pipeline)
it is applied to whichever agent is about to run rather than written onto the
agent row, so a shared agent keeps its own policy everywhere else it is used.
Leave it on *each agent's own policy* to change nothing.

::: tip A gate you can answer from the board
When a step does stop at a permission request, the approve / deny card appears
**on the workflow board**. That matters most for an [inline
step](#inline-steps-one-off-work-that-isnt-an-agent): its agent is hidden from
the Agents section, so the board is the only place its decision can be made.

Waiting on a permission does **not** pause the run. The turn is still alive and
picks up the moment you answer, so there is nothing to resume — which matters
because a step often makes several tool calls, and pausing for each one would
mean approving, resuming, approving, resuming. Only a question the agent
genuinely *raised* parks the pipeline.
:::

## One voice for the whole pipeline

A workflow can select an **Assistant role**, applied to every step's agent while
the workflow runs. Because agents are shared and reusable, the role is applied at
launch rather than stamped onto the agent rows — so the same `Summariser` can be
formal in one workflow and blunt in another. Leave it unset and each agent keeps
its own role.

## Notifications

A pipeline that runs in the background is only useful if it can reach you. A
workflow now raises a notification when it:

- **needs you** — parked on a [human approval](#human-approval-checkpoints). This
  one shows even when the app is focused, because the run is *blocked* until you
  answer;
- **finished**, or
- **failed**.

Step-to-step progress stays silent, and each transition notifies once, so a run
that emits many updates doesn't nag. The `workflow.changed` event carries the run
status and workflow name so the client can raise the notice without re-fetching.

## Running and monitoring

The workflow **detail board** shows the sequence as a horizontal strip of step
nodes, each with a live status ring — **done**, **active**, **failed**, or
**pending** — plus a lifecycle bar:

- **Run** starts (or restarts) the workflow from the first runnable step. On a
  re-run, each step agent's **artifacts are cleared** first so the fresh pass
  isn't polluted by the previous one's output.
- **Pause / Resume** halt the coordinator between steps and pick back up.
- **Cancel** stops the run.

Clicking a step opens a **modal** that drills into that agent's run — its
timeline, artifacts, and answer — while the workflow stays live in the
background. From there you can jump to the full agent in agents mode.

### Run history and the step trace

Every execution is recorded as a **run**. The detail board's **run header**
surfaces the selected run's status, trigger, live current step, elapsed time and
percent complete, and a **run picker** lets you scroll back through past
executions without leaving the page. A collapsible **Run trace** below the strip
renders the run as an append-only timeline — one row per step **attempt**,
showing what each step **received** (the input context handed down from earlier
steps) and **produced** (its output), plus gate verdicts and per-attempt
duration.

Because a [gate loop-back](#gates-and-loop-back) re-drives an earlier step, each
retry appends a **fresh attempt row** (badged `attempt 2`, `attempt 3`, …)
rather than overwriting the previous one — so the trace reads as a faithful,
inspectable record of how the pipeline actually converged. The step modal mirrors
this: it shows the **Input received** by that step and its full **Run history**
across every attempt and run.

An attempt that was opened but never ran is marked **`Superseded`** and greyed
out. Entering a step always opens a fresh trace, so a row left behind by a
process that stopped mid-step would otherwise show as a step forever in flight;
superseding it keeps the trace honest and its spend out of the run total.

### Replaying a single step

Every finished, agent-backed row in the trace carries a small **replay** icon
(⟳). It re-runs *that one step* on the **exact input it first saw** — the same
kickoff preamble, the same brief, the same upstream hand-off — and **advances
nothing**: no later step runs, and the run keeps its own outcome.

That's what makes it different from [Retry](#getting-a-stopped-run-moving-again),
which exists to get a *stopped run* moving and carries on through the rest of the
pipeline. Replay is for interrogating one step, so it's offered on a step that
**succeeded** too: take a second sample from a non-deterministic model, or check
what the step does now that you've tightened its instructions or narrowed its
tool servers — without re-running (and re-paying for) everything around it.

The replay lands in the same run trace, badged `replay` in indigo rather than
`attempt N`, and its spend rolls into the run total like any other turn. It is
deliberately invisible to the coordinator's own bookkeeping: it never becomes
"the attempt that failed" for a later retry, never counts as a pipeline attempt,
and is never mistaken for a stalled step by the [watchdog](#the-stall-watchdog).

Because a replay drives the step's agent directly, it's refused while the run is
still in flight (running, paused, or awaiting approval) — stop the run first. An
[approval checkpoint](#human-approval-checkpoints) ran no agent, so it has
nothing to replay.

Run traces are durable (backed by the `workflow_runs` / `workflow_run_steps`
tables) and fetched via `GET /api/workflows/{id}/runs`.

Individual runs are **deep-linkable**. The URL tracks the run you're looking at —
`/workflows/<id>/run/latest` follows the live/newest run (and auto-advances when a
new one fires), while `/workflows/<id>/run/<n>` pins run number `n` so you can
bookmark or share it; a pinned run stays put even as later runs execute. Scrolling
the run picker rewrites the URL to match.

When the coordinator drives a step, the agent runs its turn **without** flagging
itself as **unread** in the [Agents](./agents.md) section. That badge is reserved
for genuinely autonomous runs — ones you start manually or that fire on a
schedule — so a busy workflow doesn't leave a trail of unread agents behind it.
You still see every step's output in the run trace and the step modal.

## The run brief: one workflow, a different subject each run

A workflow definition is meant to be **generic and reusable** ("analyse it →
review it → report it"). What changes run to run is the *subject*. That's the
**run brief**: an optional free-text input you attach when you start a run.

Click the caret next to **Run** to open the brief composer, describe what this
particular run is about — the file to analyse, the topic to research, the
constraints to honour — and hit **Run with brief** (or `⌘↵`). Leave it empty and
the pipeline runs exactly as before: **fully autonomous**, on its steps' own
objectives.

```
Analyse /data/q3-sales.csv — focus on the EMEA region and
flag anything below target.
```

The brief leads **every step's** kickoff preamble, ahead of the upstream
hand-off, framed as the run's primary subject. That matters beyond step one: a
[gate](#gates-and-loop-back) three stages down can judge the work *against what
was actually asked for*, a loop-back re-drives the producer with the brief still
attached, and a final reporting step knows which file it was ever about — without
any step having to re-forward it by hand.

The brief is stored on the run, so it shows on the run header (and in each step's
**Input received**) when you scroll back through history — a past run is
self-explanatory rather than a mystery result. Runs started **on a schedule**
carry no brief by design; a **webhook** may supply one by posting a body
(`{"input": "…"}`, or any JSON/text payload, handed over verbatim).

## Getting a stopped run moving again

A run stops for two different reasons, and each has its own way forward.

- **Blocked** — a step's agent raised a question it couldn't answer alone. The
  run parks and the control turns amber, showing the question with a box to
  answer it. The answer is injected into the step's kickoff, so the retry has
  what it was missing; resuming without one simply re-runs the step.
- **Failed** — the step that broke wears a **Retry this step** button on its own
  card. It re-drives *that step* as a fresh attempt on the **same run** and
  carries on from there, so the good steps before it are neither thrown away nor
  paid for twice. The attempt is appended to the run trace with a bumped
  attempt badge, exactly like a gate loop-back. **Guidance** in the toolbar adds
  a note for the retry when the agent can't diagnose the failure itself.

Re-driving a step first **releases whatever parked it** — an unanswered
permission gate is rejected and the old turn aborted — so the retry starts on an
idle session instead of queueing behind the thing it was meant to fix.

Retry is about the *run*. When you only want another take on **one step** —
including one that succeeded — use
[replay](#replaying-a-single-step) instead: it re-runs that step on the same
context and leaves the rest of the pipeline alone.

## What a step actually did

Every attempt in the run trace carries an **Activity** section: the same
timeline the [Agents](/features/agents) cockpit renders — tool calls with their
arguments and output, reasoning, assistant messages, lifecycle hooks — sliced to
that attempt's own window. It's the difference between "the step stalled" and
"the step asked to run `workiq-do_action` and nobody approved it".

Activity is fetched on demand, so opening one attempt doesn't load the rest. In
a finished attempt, a tool call that never terminated is reported as
**interrupted** (or **never approved**) rather than left spinning — nothing can
still be running in an attempt that has ended.

## Archiving

A workflow archives like a topic, chat or agent: **Archive** on the detail board
hides it from the gallery while keeping its definition *and* its run history.
Restore or permanently delete it from the shared **Archive** panel, which now has
a Workflows tab. An archived workflow stops counting as a live reference, so it
disappears from the "used in workflows" list on the agents it referenced.

Deleting a workflow never deletes the reusable agents it pointed at — only the
private vessels belonging to its own inline steps.

## Sharing a workflow

A workflow can be **exported to YAML** — its steps, its wiring, and the agents
those steps use — then imported elsewhere. On import, any agent whose name
already exists gives you the choice to reuse it, replace it, or keep both. See
[import & export](/features/transfer).

## Triggers and scheduling

A workflow can be created **without running it** — it sits in `draft`/`idle`
until something triggers the first step:

- **Manual** — the **Run** button, optionally with a
  [run brief](#the-run-brief-one-workflow-a-different-subject-each-run).- **Schedule** — the schedule editor is the same recurrence control scheduled
  topics and agents use: either an interval ("every *N* minutes / hours / days")
  or a **time of day on chosen weekdays**. The scheduler fires the workflow like
  any other recurring job.
- **Webhook** — mint a token to expose a `POST /api/workflows/hooks/{token}`
  URL that kicks off the run. Copy it from the **Webhook** button, or revoke it
  from the bin beside it (revoking breaks the URL immediately, so it asks first).
  Any body you post becomes the run's brief.

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
| `POST /api/workflows/{id}/run-steps/{stepRunId}/replay` | [Replay](#replaying-a-single-step) one attempt on its recorded input, advancing nothing (409 while the run is in flight) |
| `POST /api/workflows/{id}/approve` \| `/reject` | Clear or bounce a human approval checkpoint (`{ "note": "…", "action": "rework\|stop\|skip" }`) |
| `POST /api/workflows/{id}/archive` \| `/unarchive` | Archive toggle |
| `PUT /api/workflows/{id}/schedule` | Configure the schedule |
| `GET /api/workflows/{id}/state` | List the pipeline's [saved state](#pipeline-state-what-a-workflow-remembers) |
| `PUT /api/workflows/{id}/state` | Upsert one value (`{ "key": "…", "value": "…" }`) |
| `DELETE /api/workflows/{id}/state/{key}` \| `/state` | Drop one key, or reset the lot |
| `POST` \| `DELETE /api/workflows/{id}/webhook` | Mint / revoke a webhook token |
| `POST /api/workflows/hooks/{token}` | Trigger via webhook (body → run brief) |
| `GET /api/transfer/workflows/{id}` | [Export](/features/transfer) the workflow (+ its agents) as YAML |

Lifecycle changes broadcast a `workflow.changed` [SSE event](/reference/api) so
the dashboard live-updates without polling.
