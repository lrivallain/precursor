---
title: Step by step
---

# Step by step

A long ask — a customer email with five questions, a brief with a dozen deliverables — is
hard to answer in one reply. **Step by step** splits it into a **tree of steps**, lets you
work each one on its own with the model, approve the results one at a time, and then
combines them into a single final output you can file back into the topic.

<Screenshot src="/screenshots/step-by-step.png" alt="A step plan: the tree of steps with their statuses on the left, the artifact of the selected step beside it" caption="A plan mid-way: one step approved, some waiting for review, the others still to do. Each step has its own artifact and discussion." />

## Starting a plan

- From a topic, type **`/steps`**: the plan is built from the topic's latest message, the
  model proposes the first steps, and the plan opens in **Step by step**.
- From the section itself (or the command palette), choose **New step plan** and paste the
  ask. Untick *Let the model propose the steps* to write them yourself.

The ask is frozen into the plan, so every later model call is grounded in it — and the
plan keeps working even if the topic is edited or deleted.

**The topic keeps the trail.** Starting a plan (with `/steps` or the button) leaves a note in
the topic's discussion, linking to the plan, and the topic's side panel lists its plans — like
side chats — with their progress. In the other direction, the plan's bar (which takes the place of the app's top bar) shows a
blue link back to the topic, and a final output sent to the topic links to the plan. The plan takes its
title from the ask: the `/steps` command is not part of it.

## Drafting the tree

Model suggestions are **proposed** until you **accept** or dismiss them; only accepted steps
are work to do. Drag steps (or use the move/indent/outdent buttons) to reorder or nest them,
edit titles and instructions, add your own, or ask the model to **suggest missing steps**
with optional guidance (in the plan's **⋯ Plan tools** menu). **Start** moves the plan to *working*.

## Working a step

Each step has:

- an **instruction** — what it must produce;
- a Markdown **artifact**, with an editor, preview and **version history** you can revert to;
- a **Discussion** next to the artifact: a chat with room to breathe (the title and
  instruction fold away above it). **Show side by side** puts it beside the artifact with a
  draggable divider, as in a live session; the tree is resizable too, and both widths are
  remembered. It knows the ask, the **source topic** (its summary and recent conversation),
  the requested output, where the plan stands, and what the **other steps** have produced
  so far (approved artifacts in full, drafts as excerpts), so you can refine the result
  without reloading everything. The same context grounds **Draft with model**.

Every step shows **one main action** for where it stands, and the rest sit in its **⋯** menu:

| The step… | Main action |
| --- | --- |
| has no artifact yet | **Draft with model** |
| has an artifact waiting for you | **Approve** |
| is approved | **Reopen** |

The **⋯** menu holds *Redraft with guidance…*, *Split into sub-steps…* (the model suggests sub-steps,
with an optional steer) and *Skip this step*. A step that has sub-steps becomes a **group**: its
status is derived from its children.

One colour per status, in the tree and on the step:

| Status | Colour | Meaning |
| --- | --- | --- |
| To do | grey | Nothing yet. |
| In progress | blue | The model is drafting it. |
| To review | amber | There is an artifact and it waits for you. A model draft lands here. |
| Approved | green | You approved the artifact (it needs a non-empty one). |
| Skipped | strikethrough | Left out of the final output. |

Editing or reverting an approved artifact sends it back to *To review* and marks the final
output **stale**, so you always know when the final output no longer matches the steps.
Changing the ask or a step's instruction after you approved it flags the step **Review**:
the approval no longer counts until you approve it again.

## Drafting with the model

**Draft with model** is the main action of a step with nothing yet (*Redraft with guidance…* in
the **⋯** menu once it has an artifact). It is the step-level trigger of the automation, and a
hint line says what comes next (draft, review, approve). Redrafting an approved step asks first, since
the step goes back to *To review*.

The draft (and the automated run) is a real turn of the step's chat, not a hidden one-shot
call: the request and the reply are recorded in the discussion, and the step inherits what the
chat can do — the plan's model and role, and its tools, with the usual approval prompts. If a
tool needs your approval, the draft waits for it in the discussion.

### The discussion changes the artifact

A reply is a conversation, so it can hold questions or alternatives. When you ask in the
discussion to write or change the artifact, the model marks the deliverable with
`<!-- artifact -->…<!-- /artifact -->` (always the whole artifact), and the app saves it at
once as a new version in *To review*, scored in the background. The discussion notes what
happened. In the transcript the deliverable shows as a **Proposed artifact** card (rendered Markdown, not
raw markers) with its own **Send to artifact** button. A reply without the markers changes nothing until you send it: the **big arrow** on the divider
between the two panes (it lights up when the model has proposed an artifact) carries the latest proposal
over, as does each card's **Send to artifact** button. The final output has the same arrow.

Nothing is overwritten silently: your unsaved edits stay in the editor (a banner offers the
newer version), and an **approved** step is never changed by the discussion — reopen it first.
The consolidation discussion works the same way and saves a provisional final output.

## The plan bar

The bar at the top of a plan shows where it stands — **Plan the steps › Work through them › Final
output › Done** — and **one next-action button** that follows it (*Review next step*, *Write the
final output*, *Send to the topic*…). The **⋯ Plan tools** menu holds *Suggest missing steps*,
*Review the plan*, *Open the final output* and *Delete plan*.

## Automation

The plan bar has an **Automation** choice:

| Mode | Who does what |
| --- | --- |
| Manual | You write and approve. The model answers when you ask; nothing is scored automatically. |
| Assisted | The model drafts when you ask and scores each draft. You review and approve. |
| Automated | **Run** drafts and scores every step that has no artifact, then a provisional final output and a global score. |

Whatever the mode, **the model never approves**: drafts land in *To review* and approval
stays yours, step by step. The run skips approved steps, steps that already have an
artifact, and an existing final output, so it can't overwrite your work. **Pause** stops it
after the step in flight and **Resume** carries on; a run interrupted by a restart reads as
paused. **Start** runs an automated plan straight away. The run is live: the step being drafted
shows a spinner in the tree and its name in the plan bar, and an **Activity** list (open while
it runs) follows each step; the page also refreshes by itself. Everything the run does is traced
in the plan's discussion, where you can talk to the model. You can add, split or reopen steps and edit instructions at any time.

### Assessments

The model **self-assesses** each draft and the whole result: a 0–100 score with what the
result covers, its evidence or sources, its assumptions and the gaps left. The score is the
model's own read, **not a calibrated probability** — read the reasons, not just the number.
The overall assessment judges the *original ask*: what it covers, what it misses and where
steps contradict each other — it is not an average of the step scores.

An assessment is tied to the work it saw. When the artifact, the instruction or the ask
changes, it is shown as **Out of date** (and the overall one as soon as any step changes)
until you assess again. An assessment is a small chip with its score; click it to read the reasons and to assess again.

## The final output

**Review the plan** (in *Plan tools*) asks the model to check the steps against the original ask and
point out gaps. Once every step is approved or skipped, **Generate the final output** combines
the approved artifacts (following the *Options* you set: standing output instructions such as
tone or format, and guidance for one run). Edit it by hand or discuss it in the consolidation
chat, then **Send to topic** to file it back into the topic the plan came from.

Generating before every step is approved — the automated run does it — is allowed but
**provisional**: the output is labelled so, the plan is not marked done, and sending it to the
topic needs an explicit confirmation and is marked provisional there. Approve the remaining
steps and regenerate for the reviewed final.

A plan is **done** once a reviewed final output exists (every step approved, not out of date):
generating it, sending a discussion reply to it, or sending it to the topic completes the plan.
For a plan with no source topic, *Mark the plan as done* in the final output's **⋯** menu does the same.

## Good to know

- Plans have at most 200 steps, nested up to 6 levels deep.
- Proposing and splitting steps, and the review, are one-shot model calls: a garbled reply
  shows an error and leaves the plan untouched. Drafting and consolidating are chat turns
  (see above), so they use the plan's model, role and tools.
- Step chats stay out of your chat list; they live inside their step.
- Deleting a topic keeps its plans; they simply lose their link.
- Plans are listed under **Step by step** in the sidebar and the command palette, and the
  state is kept in sync across windows (`step-plan.changed` on
  [`/api/events`](/reference/api#real-time-events)).
