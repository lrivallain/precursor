---
title: Step by step
---

# Step by step

A long ask — a customer email with five questions, a brief with a dozen deliverables — is
hard to answer in one reply. **Step by step** splits it into a **tree of steps**, lets you
work each one on its own with the model, validate the results one at a time, and then
**consolidates** them into a single final answer you can file back into the topic.

<Screenshot src="/screenshots/step-by-step.png" alt="A step plan: the tree of steps with their statuses on the left, the artifact of the selected step beside it" caption="A plan mid-way: two steps validated, one waiting for review, the others still to do. Each step has its own artifact and discussion." />

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
with optional guidance. **Start** moves the plan to *working*.

## Working a step

Each step has:

- an **instruction** — what it must produce;
- a Markdown **artifact**, with an editor, preview and **version history** you can revert to;
- a **Discussion** next to the artifact: a chat with room to breathe (the title and
  instruction fold away above it). **Show side by side** puts it beside the artifact with a
  draggable divider, as in a live session; the tree is resizable too, and both widths are
  remembered. It knows the ask, the **source topic** (its summary and recent conversation),
  the requested output, where the plan stands, and what the **other steps** have produced
  so far (validated artifacts in full, drafts as excerpts), so you can refine the result
  without reloading everything. The same context grounds **Draft from discussion**.

The model can **draft the artifact**, and can **suggest sub-steps** when a step should be
split: use **Split** next to the status buttons (add a steer in its popover if you want).
A step that has sub-steps becomes a **group**: its status is derived from its children.

| Status | Meaning |
| --- | --- |
| Proposed | A model suggestion waiting for you. |
| Open | Accepted, not started. |
| In progress | Being worked. |
| To validate | Has an artifact and waits for your review. A model draft lands here. |
| Validated | You approved the artifact. It needs a non-empty artifact. |
| Cancelled | Left out of the final output. |

Editing or reverting a validated artifact sends it back to *To validate* and marks the final
output **stale**, so you always know when the consolidation no longer matches the steps.
Changing the ask or a step's instruction after you validated it flags the step **Review**:
the approval no longer counts until you validate it again.

## Drafting with the model

**Draft with model** sits in each step's action row (**Redraft** once it has an artifact; the
caret adds a steer for this run). It is the step-level trigger of the automation: in an
assisted or automated plan it is the main action of a step with nothing yet, and a hint line
says what comes next (draft, review, validate). Redrafting a validated step asks first, since
the step goes back to *To validate*.

The draft (and the automated run) is a real turn of the step's chat, not a hidden one-shot
call: the request and the reply are recorded in the discussion, and the step inherits what the
chat can do — the plan's model and role, and its tools, with the usual approval prompts. If a
tool needs your approval, the draft waits for it in the discussion.

### The discussion changes the artifact

A reply is a conversation, so it can hold questions or alternatives. When you ask in the
discussion to write or change the artifact, the model marks the deliverable with
`<!-- artifact -->…<!-- /artifact -->` (always the whole artifact), and the app saves it at
once as a new version in *To validate*, scored in the background. The discussion notes what
happened. A reply without the markers changes nothing: **Send the latest reply to the artifact**
promotes one by hand. It is the big arrow on the divider between the two panes when they
sit side by side, and a bar at the top of the discussion otherwise (the consolidation has
the same, towards the final output).

Nothing is overwritten silently: your unsaved edits stay in the editor (a banner offers the
newer version), and a **validated** step is never changed by the discussion — reopen it first.
The consolidation discussion works the same way and saves a provisional final output.

## Automation

The plan bar has an **Automation** choice:

| Mode | Who does what |
| --- | --- |
| Manual | You write and validate. The model answers when you ask; nothing is scored automatically. |
| Assisted | The model drafts when you ask and scores each draft. You review and validate. |
| Automated | **Run** drafts and scores every step that has no artifact, then a provisional final output and a global score. |

Whatever the mode, **the model never validates**: drafts land in *To validate* and approval
stays yours, step by step. The run skips validated steps, steps that already have an
artifact, and an existing final output, so it can't overwrite your work. **Pause** stops it
after the step in flight and **Resume** carries on; a run interrupted by a restart reads as
paused. Everything the run does is traced in the plan's discussion, where you can talk to the
model. You can add, split or reopen steps and edit instructions at any time.

### Assessments

The model **self-assesses** each draft and the whole result: a 0–100 score with what the
result covers, its evidence or sources, its assumptions and the gaps left. The score is the
model's own read, **not a calibrated probability** — read the reasons, not just the number.
The overall assessment judges the *original ask*: what it covers, what it misses and where
steps contradict each other — it is not an average of the step scores.

An assessment is tied to the work it saw. When the artifact, the instruction or the ask
changes, it is shown as **Out of date** (and the overall one as soon as any step changes)
until you assess again. The assessment shows its score first; open it to read the reasons.

## Consolidating

**Review plan** asks the model to check the steps against the original ask and point out gaps.
Once every step is validated or cancelled, **Consolidate** drafts the final output from the
approved artifacts (following the output instructions you set, such as tone or format). Edit
it by hand or discuss it in the consolidation chat, then **Send to topic** to file it back
into the topic the plan came from.

Consolidating before every step is approved — the automated run does it — is allowed but
**provisional**: the output is labelled so, the plan is not marked done, and sending it to the
topic needs an explicit confirmation and is marked provisional there. Validate the remaining
steps and regenerate for the reviewed final.

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
