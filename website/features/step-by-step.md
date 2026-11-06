---
title: Step by step
---

# Step by step

A long ask — a customer email with five questions, a brief with a dozen deliverables — is
hard to answer in one reply. **Step by step** splits it into a **tree of steps**, lets you
work each one on its own with the model, approve the results one at a time, and then
combines them into a single final output you can file back into the source topic or chat.

<Screenshot src="/screenshots/step-by-step.png" alt="A step plan: the tree of steps with their statuses on the left, the artifact of the selected step beside it" caption="A plan mid-way: one step approved, some waiting for review, the others still to do. Each step has its own artifact and discussion." />

## Starting a plan

- From a topic, type **`/steps`**: the plan is built from the topic's latest message, the
  model proposes the first steps, and the plan opens in **Step by step**.
- In a topic or chat with Step-by-step MCP exposure enabled, the model can also delegate a
  suitable request and return the result to that conversation.
- From the section itself (or the command palette), choose **New step plan** and paste the
  ask. Untick *Let the model propose the steps* to write them yourself. Optionally pick a
  **topic**: its discussion becomes context for the model, and with no ask typed the topic's
  latest message is used.

The ask is copied into the plan, so every later model call is grounded in it — and the
plan keeps working even if its source conversation is edited or deleted. You can edit the
plan's ask later; its original conversation trace stays unchanged.

**The conversation keeps the trail.** Starting a plan leaves a quiet **Request sent to
Step by step** activity receipt in its topic or chat, with
the existing checklist icon, a blue link to the plan and an immutable copy of the
**original prompt** under a subtle quote rule. A thin outline in the feature's teal
colour surrounds the whole receipt, including its heading, link and prompt.
Text after `/steps` is the submitted ask (without the command prefix); a bare `/steps`
or the topic button uses the latest non-empty user ask
and labels the snapshot **From topic message**. The receipt records the full effective text,
not a reconstruction from the plan's later ask.

Short prompts are readable immediately. Longer ones show a compact excerpt with **Show
all** / **Show less**, including keyboard activation; nothing is truncated in storage.
Prompt text is shown literally, so Markdown, HTML and plan-like headers remain part of
the prompt rather than changing the receipt. Older link-only traces still open their plans
and explain that no original prompt was recorded; they are not backfilled from a mutable ask.

Returned **Final output** notes have a different role: a bordered Markdown document with a
soft Step by step teal background matching the receipt outline, distinct from ordinary
discussion. Its content leads, with quiet checklist metadata and version/provisional labels.
The plan link sits in the document footer, without repeating its title above a heading
already supplied by the output. The original prompt remains secondary and directly readable.
Hover the returned document and choose the **Copy formatted** envelope to paste it into
an email with its layout intact. Both the formatted copy and its plain-text fallback
contain only the document, without the plan metadata or footer link.

<Screenshot src="/screenshots/step-plan-traces.png" alt="A Step by step request with an in-progress task tree and a provisional, versioned final output" caption="The request is an activity receipt; the returned output is the document. Both keep their link to the plan." />

The topic's side panel lists its plans — like side chats — with their progress. In the
other direction, the plan's bar (which takes the place of the app's top bar) shows a
blue link back to the topic, and a final output sent to the topic links to the plan. The plan takes its
title from the ask: the `/steps` command is not part of it.

A plan created on its own can be tied to a topic later: use **Link a topic** in the bar (or
*Change the topic…* / *Unlink the topic* in **⋯ Plan tools**). The topic then gets the same
link receipt and side-panel entry, and its discussion feeds later drafts and chats. A link added
after creation does not claim to record the original prompt. While a plan has no
topic, *Send to topic* is not shown. Relinking to another topic does not move the *posted*
marker of a final output already filed in the first one. The plan tool separately indicates whether
its current final has a recorded, still-present copy in the currently linked conversation; a missing
record does not rule out copies sent separately.

## Drafting the tree

Model suggestions are **proposed** until you **accept** or dismiss them; only accepted steps
are work to do. Drag steps (or use the move/indent/outdent buttons) to reorder or nest them,
edit titles and instructions, add your own, or ask the model to **suggest missing steps**
with optional guidance (in the plan's **⋯ Plan tools** menu). **Start** moves the plan to *working*.

## Working a step

Each step has:

- an **instruction** — what it must produce;
- a Markdown **artifact**, with an editor, preview and **version history** you can restore (restoring just moves the *current* marker, it never adds a version);
- a **Discussion** next to the artifact: a chat with room to breathe (the title and
  instruction fold away above it). **Show side by side** puts it beside the artifact with a
  draggable divider, as in a live session; the tree is resizable too, and both widths are
  remembered. It knows the ask, the **source topic or chat** and recent conversation,
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

Editing or restoring an older version of an approved artifact sends it back to *To review* and marks the final
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
tool needs your approval, the draft waits for it in the discussion. Prompts from an automated
run are labelled **Step-by-step automation** rather than attributed to you. Its review feedback
is a fallible suggestion, not a new user requirement; your own guidance remains identifiable.
Older draft prompts may not have a recorded origin, so the model is told that their source is
uncertain instead of assuming they came from you. Assessments receive the discussion as
background evidence, including available tool results and images, rather than continuing the
earlier drafting dialogue.

### Skills and tools

The step's chat lists your active skills (when the `skills` MCP section is exposed) and tells the
model to load a matching one with `get_skill` before improvising. A skill named in the step's title
or instructions ("use the deckforge skill", or `/deckforge`) is called out as required, and `/skill`
mentions in the instructions are expanded. When a step produces a file, the artifact is the file's
path plus how to open it, not its content.

A model that delegates such a job to a plan does not load the skill or run the commands itself: it
names the skill in the ask, the step's draft loads and runs it, and the result comes back as the
file's path, which is handed over (not pasted) in the final output.

### The discussion changes the artifact

A reply is a conversation, so it can hold questions or alternatives. When you ask in the
discussion to write or change the artifact, the model marks the deliverable with
`<!-- artifact -->…<!-- /artifact -->` (always the whole artifact), and the app saves it at
once as a new version in *To review*, scored in the background. The discussion notes what
happened. In the transcript the deliverable shows as a **Proposed artifact** card (rendered Markdown, not
raw markers) with its own **Send to artifact** button. A reply without the markers changes nothing until you send it: the **big arrow** on the divider
between the two panes (it lights up when the model has proposed an artifact) carries the latest proposal
over, as does each card's **Send to artifact** button. The final output has the same arrow.
Once a proposal is in the artifact, its card reads **Applied as vN** (or *earlier* if the
artifact has moved on), its button is disabled and the arrow goes idle until a new proposal arrives.

The discussion is live: a model turn started from **Draft with model**, a run or the final
output shows up in the chat as it streams, and a new version becomes the current one at once,
with no reload. Assessments the model runs are announced in the chat too (*Assessing … version N…*),
and a failed one says so. Saving identical content normally reuses its version; an accepted
follow-up can start a distinct final-output revision even when its text is unchanged.

Nothing is overwritten silently: your unsaved edits stay in the editor (a banner offers the
newer version). On an **approved** step, a new deliverable from the model is applied as a new
version and reopens the step for your approval; the approved version stays in its history, and
only you approve.
The consolidation discussion works the same way and saves a provisional final output.

## The plan bar

The bar at the top of a plan shows where it stands — **Plan the steps › Work through them › Final
output › Done** — and **one next-action button** that follows it (*Review next step*, *Write the
final output*, *Send to the topic*…). Once assessed, the final output's score is shown in the bar;
an out-of-date score is struck through, and clicking it opens the final output. The **⋯ Plan tools** menu holds *Suggest missing steps*,
*Review the plan*, *Open the final output*, *Archive plan* and *Delete plan*.

### Archive and restore

Use **⋯ Plan tools → Archive plan** to hide an inactive plan from the normal lists and
its source's active plans. Pause a running plan and let its current work stop first.
Its steps, artifacts, discussions, final versions and conversation receipts are kept;
an archived plan opens read-only. Restore it from the plan's menu or from **Archive →
Plans**. The Archive also lets you permanently delete a plan after confirmation without
deleting its source conversation.

## Automation

The plan bar has an **Automation** choice:

| Mode | Who does what |
| --- | --- |
| Manual | You write and approve. The model answers when you ask; nothing is scored automatically. |
| Assisted | The model drafts when you ask and scores each draft. You review and approve. |
| Automated | **Run** drafts and scores each step that has no artifact, one at a time, then a provisional final output and a global score. |

For plans you start yourself, drafts land in *To review* and approval stays yours, step by
step. Model-delegated plans can also validate drafts automatically as described below.
The run skips approved steps and steps that already have an artifact. It keeps an unchanged
existing final output; an accepted amendment rebuilds the stale final as a new version.
**Pause** stops it after the work in flight, including final consolidation or assessment, and
prevents automatic filing until **Resume**; a run interrupted by a restart reads as paused.
**Start** runs an automated plan straight away. The run is live: the step being drafted
shows a spinner in the tree and its name in the plan bar, and an **Activity** list (open while
it runs) follows each step; the page also refreshes by itself. Everything the run does is traced
in the plan's discussion, where you can talk to the model. You can add, split or reopen steps and edit instructions at any time.

### Delegation by the model

A model can start and follow a plan itself when the **Step-by-step plans** section is
exposed in *Settings → MCP exposure* (off by default). It then has `create_step_plan`,
`list_step_plans`,
`get_step_plan` (with an optional `wait_seconds` for a status check),
`amend_step_plan` for a follow-up, `edit_step_plan` (add, update, delete, skip, accept all), `run_step_plan` and
`pause_step_plan`. Inside a conversation these tools are pinned to that conversation:
the app overrides any other topic or chat the model names, so it can only create,
amend, run or pause its own plans. The system prompt tells the model to delegate substantial, multi-stage
work when separate drafting and review would help. This can include interdependent
deliverables with shared constraints or work requiring skills and tools; a short answer
doesn't need a plan merely because it has several small parts. There is no fixed
deliverable-count rule. The choice is model-generated and is not guaranteed, and an
explicit request not to delegate is respected. By default the model
can never approve a step. When a model delegates a plan, the **source conversation's
autonomy** (the *Autonomous / Balanced / Manual* chip) decides who reviews:

For a plan created or amended in the same conversation, Precursor supplies the current
topic or chat if the model omits it, and includes the actual originating user message
alongside the model's delegated scope, even when the turn pauses for an approval.
Explicitly supplied sources are still checked. This keeps stated constraints available if
the model shortens its tool request, without asking the plan to take on unrelated parts of
a broader message. The model is instructed to honor the constraints, but its proposed steps
should each address their own result, retain applicable exact limits, and leave general
assembly and filing to the app. A step can still create or update a shared file when the
work requires it. Suggestions and output are model-generated and should be checked. An empty plan receipt says
**No steps yet** and links to the plan, rather than implying a proposal is still running
after a failure.

| Conversation autonomy | What happens |
| --- | --- |
| **Autonomous** | The run reviews the drafts and final output. If they pass, it files the final output in the source conversation and wakes its model; otherwise it stops for review. |
| **Plan** | Nothing runs. The model only drafts and amends the plan; you review it and press **Start the run** on the card, then do one final review (like Balanced). |
| **Balanced** | The drafts are validated, you do one final review of the output and send it to the source conversation. |
| **Manual** | You validate every step, then send the output to the source conversation. |

By default, the delegated plan's discussions also inherit that recorded autonomy for
tool approvals, and their rules include those of the source conversation. A
discussion's shield pill can override the policy; choosing **Default** returns
to the plan's policy. Discussion-specific rules take precedence over source
rules, which take precedence over global rules. Plans you start yourself keep
the normal chat default. See [Tool approvals](./approvals.md).

In Plan, Balanced and Manual modes the conversation's model is woken when you send a
completed output, and carries on by itself. A forced provisional output does not wake
it. Hand-back waits while the source is generating or has an unresolved approval; if that
turn read the exact final version and responded after it was filed, it is not asked to review
it again, even if it read that version just before filing. If that final is superseded, relinked,
or its filed message changes while hand-back waits, the outdated review is skipped. If an automatic review fails,
the plan stays in *Consolidating* with a note. While an automated run is still working,
validated steps and their individual scores are not yet the assessed final output. The
source model is instructed not to post step drafts as the final or make a second copy;
it is also instructed to report a newly started run once and end its turn rather than
repeatedly wait for it, unless you explicitly ask it to wait. The card tracks progress
while the app handles the eventual review and hand-back. While a run is active, the card
also refreshes periodically in case an update event is missed; a refresh failure is shown
on the card. Models may still repeat content
or check progress despite that guidance. A hand-back receives the exact current final
assessment score when available, labelled as a model estimate; otherwise it is instructed
not to invent a score. It is also instructed to check the actual result instead of treating
a high score as proof, and to acknowledge unverified process restrictions. Its response is
still model-generated and can be wrong.

### Follow up on an existing result

Ask in the original topic or chat for a change to an earlier result, such as adding a
slide to a deck. When the model finds a related plan, it can read its prior steps and
outputs, add just the new work, and revise the output instructions. The follow-up is
recorded as a numbered revision with a live Tasks card in the conversation. The original
ask, approved steps and earlier final-output versions remain available; the previous final
and its assessment are marked out of date until the revised result is prepared and assessed.
Earlier step assessments are also marked out of date because they did not see the new request;
their artifacts, versions and approvals are preserved. Later requests replace earlier requirements
only where they explicitly change them.

The source conversation's **current autonomy** decides what runs: **Plan** waits for
you to press **Start the run**, **Manual** leaves each new step for approval, and
**Balanced** or **Autonomous** review and continue as above. A plan with a live run
cannot be amended until that run has stopped. If saving the amendment succeeds but
starting its run fails, the amendment remains saved and the failure is reported.

Final-output version history stores the text of each delivery, including a distinct
revision when the text happens to be identical. It does **not** snapshot files named by
that text. For file work, amendment steps are instructed to inspect the current file and
prefer a revised copy unless you explicitly request an in-place edit; verify or back up
important files separately.

### The plan in the discussion

The topic or chat that started a plan shows a live **Tasks** card under the receipt: each step with
its status, score chip and a spinner on the one being drafted, an approved count, and
**Pause**/**Resume** for automated plans. When every step is approved, the card offers
**Generate the final output** if none exists, **Regenerate the final output** if the existing
one needs regeneration, or **Send to the topic** / **Send to chat** when a current final is
ready for review. A paused plan directs you to review blockers in the plan before resuming,
instead of offering final actions from the card. Regenerating a delegated plan does not complete it;
sending does. It collapses once the plan is done; the steps
themselves are still worked in the plan. A paused card shows the latest recorded activity
and directs you to review any blocker in the plan before resuming.

### Assessments

The model **self-assesses** each draft and the whole result: a 0–100 score with what the
result covers, its evidence or sources, assumptions, non-blocking observations and gaps left. The score is the
model's own read, **not a calibrated probability** — read the reasons, not just the number.
Each step is assessed against its own instruction: an intermediate HTML table or section does
not need to be a separate saved file unless that step asks for one. The final deliverable's
requirements are not treated as due at every intermediate step. For factual or technical work,
the assessor is instructed to check material claims against supplied sources or with available
read-only tools; citations and confident wording alone are not evidence, and unsupported
quantitative or comparative claims should be called out. Repeating a claim in a generated
step, final output or file does not establish that it is true; a file check confirms only
what it actually measured. The assessor also considers requested brevity and is instructed
not to infer that no file or lookup occurred just because none appears in the supplied
evidence. The final assessor also receives the current conversation's recorded filing status at
review time, so it need not infer publication from an inline draft. This remains a model review,
not a guarantee of correctness.
The assessment panel warns that model-generated reasons can be wrong and that an unlisted action
cannot be ruled out from incomplete tool records. Coverage and evidence are labelled as the
model's own reports, separately from the app's measured checks and its record of reviewer tool calls.
The overall assessment judges the *original ask and accepted follow-ups*: what it covers, what it misses and where
steps contradict each other — it is not an average of the step scores. It says which scope it
read: before a final output exists it is a **Plan readiness** score (the steps' artifacts only,
explicitly *not* an assessment of a final deliverable); once a final output exists, assessing
reads that text against the ask, the output instructions and the approved artifacts and is
titled **Final output assessment**. Editing the final output makes that assessment out of date,
and a readiness score from before the final existed is labelled as steps-only until you assess again.

When the deliverable is a **file** (a deck, a report) the assessor doesn't judge the bare path. The
app first verifies what it can: the file exists and isn't empty, an HTML or PowerPoint deck's slide
count (compared with a count in the ask, such as "4 slides"), JSON that parses, and a bounded
excerpt of the content. The measured checks appear under **Verified by the app**; a failed check adds a gap and
caps the score (missing or empty file 25, unreadable file 40, wrong count 60). A file's content is
only read when a tool of the plan mentioned its path, and never for hidden folders.
For step and final assessments the model also receives recent recorded tool responses from the
relevant plan discussions, up to 12 per discussion and 64 in total. These are bounded conversation
records, not a complete audit: they can include other turns and cannot prove that an unlisted action
did or did not happen.
When the final output names or links to a file and inspection is permitted, its assessor is
instructed to inspect that file with read-only tools or compare it with the app-provided excerpt
and checks; if it cannot inspect the file, it must say so instead of claiming the contents were
verified. The final review treats
the original ask and output instructions as authoritative, checks explicit measurable constraints,
and does not turn intermediate step titles into new final requirements.

In **Autonomous** and **Balanced** runs a failed check is acted on, not just shown: the step is
redrafted once with the failures as instructions before it can be validated. A step scoring under
60, or whose assessment identifies unmet requirements, is also redrafted once. The model is told
to check assessment feedback against the request instead of treating it as a new requirement;
it can still act on mistaken feedback. A final output that
names a file that doesn't exist, or doesn't give the path of the file the steps built (pasting the
content instead), is rebuilt once with the real paths. If it is still failing, the plan is left for
your review and nothing is filed in the topic.

Automatic runs now apply the check to each step **before starting the next one**, so later work
doesn't quietly depend on a failed or incomplete draft. If a failed check, assessment gap or
reported contradiction remains after its retry, or the score is still under 50, the run pauses at
that step; resuming will continue to hold there until a person reviews it. A score from 50 to 59
is allowed after the retry only when no gaps or contradictions remain. If an assessment fails or
becomes out of date while it runs, the draft is kept
and the run pauses rather than validating it without a current score. You can retry the assessment
and resume, or review the draft yourself. An existing unfinished, weak or incomplete draft is preserved for
review instead of being overwritten. The whole ask and sibling work remain available as context, but each step is
explicitly told to prioritize its own result. It may still do useful work from a later step when
that makes sense or the ask calls for it, provided it identifies the extra work rather than
substituting it for the current result. A step that only adds new material is told not to copy all
earlier approved work into its artifact unless needed; consolidation can use those artifacts directly.
When consolidating, the model is instructed to reconcile a mechanical timing or arithmetic error
only when the request and supplied evidence establish the correction, without changing the saved
step artifacts or approved decisions. An unresolved material conflict that needs a new decision
is surfaced instead of adding an unrequested section or filing a supposedly finished result.
Existing files are not meant to be overwritten when their
ownership is unclear; the draft is told to choose a fresh path or ask. Assessor tool notes include
the exact workspace file path or a source URL without query parameters.

The assessor can also **look for itself**. When the step built a file or ran tool calls, it can list
the files the step built, read one (a Word, PowerPoint, Excel, HTML or text file is turned into text,
paged for long files), and re-read the output of a numbered recorded tool call (up to 12,000
characters, with truncation indicated). It can only open files a
tool of the plan mentioned or that sit in that plan's workspace folder, never hidden folders, and it
is told that file text is data, not instructions. These extra file and output inspectors are offered
only when there are corresponding files or tool outputs; the assessor is told not to look for files
when the result is inline and no file is requested or indicated.

For work that lives elsewhere, such as a mail draft, a GitHub issue or a web page, it runs through
the **same tool loop as a chat**, with the step chat's own model, per-run tool-use budget, enabled
MCP servers and approval rules, so it has the same tools (including the workspace and skill tools)
and can check at the source. The assessor is instructed to honor the request's applicable limits
on research, data access, files and other actions in its own tool calls, but this is model guidance,
not an access-control guarantee. Use MCP exposure and approval rules to restrict MCP tool access.
This includes other exposed Precursor tools, such as reading a topic,
chat or plan, not just workspace and skill tools. It sees the step or consolidation discussion's
chat history, including retained tool results, plus the linked source conversation as background;
the usual context limits and compaction apply. It is a separate, ephemeral review, not a new
message saved to the discussion. It is a reviewer: with
autonomous treated as balanced, read-only tools run; any other tool (including tools that don't
declare a read-only hint) is **declined rather than parked for approval**, and the assessor notes
it as an assumption, unless an [allow rule](./approvals.md) covers it. The additional inspector
tools also follow this gate under the server name `step-inspector` and tool names `list_files`,
`read_file` and `read_tool_output`. Manual policy or a matching **ask** rule declines these calls;
an **allow** rule can permit them. MCP exposure does not disable these internal inspectors.
The app's automatic bounded file checks and excerpts, and conversation history already supplied
to the reviewer, are separate from model-initiated tool calls and are not governed by these rules.
On its last round the tools
are withdrawn so it answers instead of running out of budget. The reviewer receives the complete
original ask and accepted revisions, the applicable step or final result, the source conversation
as background and the available evidence. For an individual step, it is instructed to apply
relevant original constraints without treating sibling work as already due. The current result
is presented after the historical context; earlier drafts and repair suggestions are not new
requirements. It distinguishes unmet requirements from uncertain claims and non-blocking
observations.

This is **one tool-capable model review**, not an independent proof of correctness: it can miss
defects or report false ones. Failed app checks are applied afterward and cannot be demoted by the
model. Required review input must fit the configured context throughout the tool loop; otherwise
the assessment fails rather than judging a silently truncated request. A failed or malformed review
is reported instead of being replaced with a tool-less judgment. What it opened or called is listed
under **Checked with tools** (for example "Called workiq__get_draft" or "Declined
workiq__send_mail"), and the measured caps above still apply.

An automated run also judges the **final output**. If the assessment identifies unmet requirements
or contradictions, the run rebuilds a final it just generated once, then assesses it again. It
preserves an existing final rather than rewriting it. If those findings remain, the score is under
50/100, or the assessment is missing or no longer matches the output, the result is not filed.
The run stops with a note and you review it, then send it yourself. Since model assessments can
be wrong, a false gap can also stop a run; a person can review and approve the work. Immediately
before an autonomous hand-back, the app
checks the assessed plan text, stored steps, assessment and source again and files the message and
its version record together. If those changed, it stops instead of sending a stale result; external
files are not snapshotted. A `/ws/<workspace>/<file>` link is read as that workspace's file.

A cover slide isn't counted against "N slides", and files the steps write in the app's own
workspaces are read like any other. A step's artifact gives the file's path plus a short summary of
what the step put in it. A transient model failure (a timeout or a 5xx) is retried twice with a
short pause, and noted in the activity, rather than pausing the whole run.

An assessment is tied to the work it saw. When the artifact, the instruction, the ask or an accepted
follow-up changes, it is shown as **Out of date** (and the overall one as soon as any step changes)
until you assess again. An assessment is a small chip with its score; click it to read the reasons and to assess again.

## The final output

**Review the plan** (in *Plan tools*) asks the model to check the steps against the original ask and
point out gaps. Once every step is approved or skipped, **Generate the final output** combines
the approved artifacts (following the *Options* you set: standing output instructions such as
tone or format, and guidance for one run). Edit it by hand or discuss it in the consolidation
chat, then **Send to topic** to file it back into the topic the plan came from.

The final output is versioned like a step's artifact. Every distinct text you save, or that the
model generates, becomes the next **Version N** (shown in the toolbar, with
*you* or *model* as its author), and **History** lists them, each with *View* and *Restore*.
The *Edit / Preview* tabs sit right above the content, and the history opens below it (scrolled
into view), in the final output as in a step's artifact. Both are the same panel: they open in
*Edit* with the resizable box and the **Refine with AI** rewrite helper, and a model-written
text (generated or sent from the discussion) never replaces your unsaved edits; it waits behind
a *newer version* banner.
Restoring an older version only moves the *current* marker, and saving a text that matches an
earlier version points back at it, so neither adds a duplicate. When you **Send to topic**, the
version that was filed gets a green **posted** badge in the history and next to the version
number, and the note in the topic names it (*final output v2*). In the topic the note is a
softly teal-tinted document with quiet **Final output** metadata, the version and, when it applies,
a *Provisional* label; the plan title links back to the plan from its footer. Later versions don't move the
badge: it stays on the version that was actually posted. Saving or restoring a different final
version after a plan is done reopens the plan for that version; saving the same version leaves
the plan done. Approved steps and earlier output versions remain intact.

If a step changes after the final output was written, the final output is **out of date**, even
once you approve the step again. It cannot be sent to the topic or complete the plan until you
regenerate it, or read it and click **Mark as reviewed** (or save your edits). The server enforces
this; there is no override for an out-of-date final output.

Generating before every step is approved — the automated run does it — is allowed but
**provisional**: the output is labelled so, the plan is not marked done, and sending it to the
topic needs an explicit confirmation and is marked provisional there. Approve the remaining
steps and regenerate for the reviewed final.

A plan is **done** once a reviewed final output exists (every step approved, not out of date).
For a directly managed plan, generating a final or adopting a discussion reply can complete it;
for a delegated plan, generation leaves it open until it is sent to the source conversation
or explicitly marked done. *Mark the plan as done* in the final output's **⋯** menu is also
available when no source conversation is linked. A reopened delegated plan can run its final
assessment again without overwriting the existing output.

## Unsaved edits

Unsaved artifact, instruction and final-output edits are kept while you move between steps, the
final output, other sections and plans in the same tab, and an amber dot on the step (or the
*Final output* row) shows where they are. A whitespace-only edit counts as an edit. The edits live
in the page: a reload or closing the tab discards them, and the browser asks first.

A title, instruction, output instruction or ask is saved when you leave the field. If that save
fails, the error is shown, your text stays in the field (and when you come back to it, with an
amber *Not saved* note and a **Retry**), and nothing is lost by navigating away.

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
