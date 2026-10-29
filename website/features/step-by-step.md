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

## Drafting the tree

Model suggestions are **proposed** until you **accept** or dismiss them; only accepted steps
are work to do. Drag steps (or use the move/indent/outdent buttons) to reorder or nest them,
edit titles and instructions, add your own, or ask the model to **suggest missing steps**
with optional guidance. **Start** moves the plan to *working*.

## Working a step

Each step has:

- an **instruction** — what it must produce;
- a Markdown **artifact**, with an editor, preview and **version history** you can revert to;
- a **Discussion** tab: a chat with room to breathe (the title and instruction fold away
  above it). It knows the ask, the **source topic** (its summary and recent conversation),
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

## Consolidating

**Review plan** asks the model to check the steps against the original ask and point out gaps.
Once every step is validated or cancelled, **Consolidate** drafts the final output from the
validated artifacts (following the output instructions you set, such as tone or format). Edit
it by hand or discuss it in the consolidation chat, then **Send to topic** to file it back
into the topic the plan came from.

## Good to know

- Plans have at most 200 steps, nested up to 6 levels deep.
- Model actions are one-shot and tool-less; a garbled reply shows an error and leaves the plan
  untouched. They use the default model, not a conversation's model preset.
- Step chats stay out of your chat list; they live inside their step.
- Deleting a topic keeps its plans; they simply lose their link.
- Plans are listed under **Step by step** in the sidebar and the command palette, and the
  state is kept in sync across windows (`step-plan.changed` on
  [`/api/events`](/reference/api#real-time-events)).
