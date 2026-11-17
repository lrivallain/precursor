---
title: Running a pipeline
---

# Running a pipeline

Start a workflow, follow it, and get it moving again when it stops.

## Triggers and scheduling

| Trigger | How |
| --- | --- |
| **Manual** | **Run**, optionally with a [run brief](#the-run-brief-one-workflow-a-different-subject-each-run) |
| **Schedule** | An interval, or a time of day on chosen weekdays. A workflow can hold [several rules](/features/scheduler#several-cadences-on-one-item) and runs on whichever comes first |
| **Webhook** | Mint a token for `POST /api/workflows/hooks/{token}`; the posted body becomes the run brief |

## The run brief: one workflow, a different subject each run

Keep the workflow generic ("analyse → review → report") and give each run its
subject. Click the caret next to **Run**, write the brief, then **Run with
brief** (`⌘↵`):

```text
Analyse /data/q3-sales.csv — focus on the EMEA region and
flag anything below target.
```

The brief leads **every** step's kickoff, so a later gate can judge against what
was asked. It is shown on the run header and available as
<code v-pre>{{run.input}}</code>. Scheduled runs have no brief.

## Watching several pipelines at once

In the gallery, a card for a **running**, **paused** or **awaiting approval**
workflow shows a progress bar: the current step and percent complete, from this
run's trace. Gate loop-backs and [replays](#replaying-a-single-step) don't
rewind or inflate it; the active step's own progress is blended in. Colour: sky
running, amber paused, violet needs you.

## The detail board

A horizontal strip of steps with status rings (**done**, **active**, **failed**,
**pending**) and the lifecycle controls:

- **Run** starts from the first runnable step (clearing step artifacts first,
  unless the workflow keeps them across runs).
- **Pause** / **Resume** act between steps. **Cancel** stops the run.

The active step shows the first line it is writing, or *Thinking…* while the
model [thinks](/features/agents-mode/running#watching-an-agent-think). Click a
step to open its agent run (timeline, artifacts, answer) in a modal.

## Run history and the step trace

<Screenshot src="/screenshots/workflow-run-trace.png" alt="A workflow run trace listing one row per step attempt, including a gate that failed and sent step 2 back for a second attempt before passing" caption="One row per step attempt. Here the gate failed, sent step 2 back — badged attempt 2 — then passed on the revision." />

The run header shows status, trigger, current step, elapsed time and percent
complete; the **run picker** lists past runs. **Run trace** has one row per
step **attempt**: what it received and produced, gate verdicts, duration, token
spend, and the agent run it launched (`run #123`). Retries add rows badged
`attempt 2`, `attempt 3`…

- `/workflows/<id>/run/latest` follows the newest run; `/workflows/<id>/run/<n>`
  pins run `n`.
- Workflow steps don't mark their agents **unread** in Agents.

### What a step actually did

Each attempt's **Activity** section shows that attempt's agent timeline (tool
calls with arguments and output, messages, thinking, errors), loaded on demand.
Use it to tell "the step stalled" from "the step was waiting on an approval".

### Replaying a single step

The ⟳ icon on a finished, agent-backed row re-runs **that step only** on the
exact input it first saw, and advances nothing. Use it on successful steps too:
to sample again or test tightened instructions without re-running the pipeline.
The replay is added to the trace (badged `replay`) and its spend to the run
total. It is refused while the run is in flight.

## When a step fails: retry, carry on, or stop

Each step has a **failure policy**:

| Policy | Effect |
| --- | --- |
| **Stop run** (default) | Stop the run |
| **Retry** | Re-run the step up to *N* times (1–10) with the failure reason injected; then stop |
| **Carry on** | Record the failure and continue (for nice-to-have steps) |

Retry budgets are **per run**. A turn that ends with **no output and no token
spend** (typically a dropped model stream) counts as a failure, so it can be
retried.

### The stall watchdog

Set a **stall watchdog** on the workflow (minutes; `0` = off, the default unless
changed in **Settings → Workflows**). A step running longer is cancelled and put
through its failure policy. Set one on any scheduled pipeline, or a hung step
keeps it `running` forever.

## Getting a stopped run moving again

| The run is… | Do this |
| --- | --- |
| **Blocked** (a step raised a question) | Answer it in the amber control; the answer is injected into the step's next attempt. |
| **Failed** | Click **Retry this step** on the failed step. It re-runs on the **same run** and continues; earlier steps aren't re-run. Add a note with **Guidance** if needed. |

Retrying first releases whatever parked the step. To re-sample one step without
continuing, use [replay](#replaying-a-single-step).

## Tool approvals for the whole pipeline

A step waiting on a tool approval holds up the run, and a scheduled or webhook
run has nobody to answer. **Tool approvals** in the workflow settings sets
**Manual / Balanced / Autonomous** for every step's agent during the run, without
changing the agents' own policies.

Permission requests appear **on the workflow board** with approve/deny, the only
place to answer them for an
[inline step](/features/workflows/building#inline-steps-one-off-work-that-isn-t-an-agent).
Waiting on a permission doesn't pause the run; the turn resumes as soon as you
answer.

## Cost

Each attempt records its token spend; the run header shows the total and each
trace row its own, so an expensive gate loop is easy to spot.

## Notifications

A workflow notifies when it **needs you** (a
[human approval](/features/workflows/building#human-approval-checkpoints); shown
even when the app is focused), **finished**, or **failed**. Each transition
notifies once; step-to-step progress is silent.

## Archiving

**Archive** hides a workflow from the gallery, keeping its definition and run
history; restore or delete it from the **Archive** panel. Archived workflows no
longer count as "used in workflows" on their agents. Deleting a workflow never
deletes reusable agents, only its inline steps' private agents.
