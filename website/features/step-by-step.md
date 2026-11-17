---
title: Step by step
---

# Step by step

Split a long ask (an email with five questions, a brief with a dozen deliverables) into a
**tree of steps**, work and approve each step with the model, then combine them into one
final output you file back into the source topic or chat.

<Screenshot src="/screenshots/step-by-step.png" alt="A step plan: the tree of steps with their statuses on the left, the artifact of the selected step beside it" caption="A plan mid-way: one step approved, some waiting for review, the others still to do. Each step has its own artifact and discussion." />

## Starting a plan

| From | How |
| --- | --- |
| A topic | **`/steps [ask]`**. With no text, the topic's latest user message is the ask. The model proposes the first steps. |
| The **Step by step** section or ⌘K | **New step plan**, paste the ask. Untick *Let the model propose the steps* to write them yourself. Optionally pick a **topic** as context. |
| A topic or chat, by the model | When **Step-by-step plans** is exposed in *Settings → MCP exposure* (off by default). See [Delegation by the model](#delegation-by-the-model). |

The ask is copied into the plan, so the plan keeps working if its source conversation is
edited or deleted. You can edit the ask later.

The source conversation gets a **Request sent to Step by step** receipt: a link to the plan
and an immutable copy of the original prompt (long prompts fold behind **Show all**), plus a
live **Tasks** card with each step's status. The topic's side panel lists its plans; the
plan's bar links back to the topic.

<Screenshot src="/screenshots/step-plan-traces.png" alt="A Step by step request with an in-progress task tree and a provisional, versioned final output" caption="The request is an activity receipt; the returned output is the document. Both keep their link to the plan." />

A plan started on its own can be tied to a topic later with **Link a topic** in the bar
(or *Change the topic…* / *Unlink the topic* in **⋯ Plan tools**). *Send to topic* is hidden
while a plan has no topic.

## Drafting the tree

Model suggestions are **proposed** until you **accept** or dismiss them. Drag steps (or use
move/indent/outdent) to reorder and nest them, edit titles and instructions, add your own, or
use **⋯ Plan tools → Suggest missing steps**. **Start** moves the plan to *working*.

Limits: **200 steps**, nested up to **6 levels**.

## Working a step

Each step has an **instruction**, a Markdown **artifact** (editor, preview, version history),
and a **Discussion**: a chat that knows the ask, the source conversation, and what the other
steps produced so far. **Show side by side** puts the discussion beside the artifact; the
divider and the tree are resizable.

| The step… | Main action |
| --- | --- |
| has no artifact | **Draft with model** |
| has an artifact waiting | **Approve** |
| is approved | **Reopen** |

The step's **⋯** menu holds *Redraft with guidance…* (asks first on an approved step),
*Split into sub-steps…* and *Skip this step*. A step with sub-steps becomes a **group** whose status comes from its children.

| Status | Colour | Meaning |
| --- | --- | --- |
| To do | grey | Nothing yet. |
| In progress | blue | The model is drafting it. |
| To review | amber | An artifact waits for you (model drafts land here). |
| Approved | green | You approved a non-empty artifact. |
| Skipped | strikethrough | Left out of the final output. |

- Editing or restoring an approved artifact sends it back to *To review* and marks the final
  output **stale**. Changing the ask or the step's instruction flags the step **Review**.
- Restoring a version only moves the *current* marker; it never adds a version.

### Drafting with the model

A draft is a real turn of the step's chat: it uses the plan's model, role and tools, with the
usual [tool approvals](./approvals.md), and is recorded in the discussion. Prompts sent by an
automated run are labelled **Step-by-step automation**.

- **Skills.** The step's chat lists your skills (when the `skills` MCP section is exposed). A
  skill named in the title or instruction (`use the deckforge skill`, `/deckforge`) is
  required, and `/skill` mentions are expanded.
- **Files.** When a step produces a file, the artifact is the file's path and how to open
  it, not the content.

### The discussion changes the artifact

Ask in the discussion to write or change the artifact and the model wraps the whole
deliverable in `<!-- artifact -->…<!-- /artifact -->`; it is saved at once as a new version in
*To review*. It shows as a **Proposed artifact** card. A reply without markers changes nothing
until you push it with **Send to artifact** on the card, or the **big arrow** on the divider.
Applied cards read **Applied as vN**.

Nothing is overwritten silently: unsaved edits stay in the editor behind a *newer version*
banner, and a new model version on an approved step reopens it for your approval.

## The plan bar

The bar shows the stage (**Plan the steps › Work through them › Final output › Done**), one
**next-action** button, the final output's score, and **⋯ Plan tools**: *Suggest missing
steps*, *Review the plan*, *Open the final output*, *Archive plan*, *Delete plan*.

**Archive plan** hides a plan from lists (pause a running plan first); an archived plan opens
read-only. Restore it from its menu or **Archive → Plans**, where you can also delete it
permanently (the source conversation is kept).

## Automation

| Mode | Who does what |
| --- | --- |
| Manual | You write and approve. Nothing is scored automatically. |
| Assisted | The model drafts when you ask and scores each draft. You approve. |
| Automated | **Run** drafts and scores every step without an artifact, one at a time, then writes a provisional final output and a global score. |

- The run skips approved steps and steps with an artifact. For plans you start yourself,
  approval stays yours.
- **Pause** stops after the work in flight and blocks automatic filing until **Resume**. A run
  interrupted by a restart reads as paused.
- A step whose check still fails after one retry (or scores under 50) pauses the run there.
  See [assessment rules](/reference/step-by-step#automatic-runs).
- Model calls that time out or return 5xx are retried twice before the run pauses.

### Delegation by the model

With **Step-by-step plans** exposed in *Settings → MCP exposure*, the model gets
`create_step_plan`, `list_step_plans`, `get_step_plan` (optional `wait_seconds`),
`amend_step_plan`, `edit_step_plan`, `run_step_plan` and `pause_step_plan`. Inside a
conversation these tools are pinned to that conversation. The model can never approve a step;
the **source conversation's autonomy** decides who reviews:

| Autonomy | What happens |
| --- | --- |
| **Autonomous** | The run reviews drafts and the final output. If they pass, it files the output in the conversation and wakes its model; otherwise it stops for you. |
| **Plan** | Nothing runs. You review and press **Start the run** on the card, then do one final review. |
| **Balanced** | Drafts are validated; you review the final output and send it. |
| **Manual** | You validate every step, then send the output. |

When you send a completed output (Plan, Balanced, Manual), the conversation's model wakes and
carries on. Delegated plans' discussions inherit the source conversation's approval policy and
rules; the shield pill overrides it. Details: [delegation](/reference/step-by-step#delegation).

### Follow-ups

Ask the original conversation to change an earlier result ("add a slide to the deck"). The
model can amend the related plan: new steps are added as a numbered revision, earlier
artifacts and approvals are kept, and the previous final output and assessments are marked out
of date. A plan with a live run can't be amended until the run stops.

::: warning Files are not versioned
Final-output history stores the text of each version, not the files it names. Back up
important files yourself.
:::

## Assessments

The model **self-assesses** each draft and the whole result: a 0–100 score with coverage,
evidence, assumptions and gaps. It is the model's own read, **not a calibrated probability**:
read the reasons. Click the score chip to read them or assess again; it shows **Out of date**
once the work changes.

- Before a final output exists, the overall score is **Plan readiness** (steps only); after,
  it is a **Final output assessment** against the ask.
- For a **file** deliverable, the app first checks it (exists, non-empty, slide count vs the
  ask, JSON parses) and lists results under **Verified by the app**. A failed check caps the
  score: missing/empty **25**, unreadable **40**, wrong count **60**.
- The assessor can use read-only tools to check at the source; non-read-only tools are
  declined unless an [allow rule](./approvals.md) covers them. What it used is listed under
  **Checked with tools**.

Full rules (retries, thresholds, inspector tools, evidence limits):
[Step by step reference](/reference/step-by-step#assessments).

## The final output

Once every step is approved or skipped, **Generate the final output** combines the approved
artifacts, following the *Options* (standing output instructions, plus guidance for one run).
Edit it by hand or in its consolidation chat, then **Send to topic**.

- It is versioned like an artifact (**Version N**, *you* / *model*, **History** with *View* and
  *Restore*). The filed version gets a green **posted** badge.
- In the conversation it appears as a teal **Final output** document linking back to the plan.
  **Copy formatted** copies just the document for email.
- If a step changes afterwards, the output is **out of date** and can't be sent until you
  regenerate it or click **Mark as reviewed**.
- Generating before every step is approved (as automated runs do) gives a **provisional**
  output; sending it asks for confirmation.
- A plan is **done** once a reviewed final exists. A delegated plan stays open until the output
  is sent (or *Mark the plan as done* in the output's **⋯** menu).

## Good to know

- Unsaved edits survive moving between steps, sections and plans in the same tab (amber dot);
  a reload discards them, and the browser asks first. A failed field save keeps your text with
  a **Retry**.
- Proposing/splitting steps and *Review the plan* are one-shot calls: a garbled reply leaves
  the plan untouched.
- Step chats stay out of your chat list.
- Deleting a topic keeps its plans; they lose the link.
- State syncs across windows (`step-plan.changed` on
  [`/api/events`](/reference/api#real-time-events)).
