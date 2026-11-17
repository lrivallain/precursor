---
title: Step by step internals
---

# Step by step: delegation and assessment rules

Detailed behaviour behind [Step by step](/features/step-by-step). Read it when you rely on
automated runs or model delegation and need to know exactly when a run stops, what the
assessor sees, and what it may do.

## Delegation

### Tools

Exposed when **Step-by-step plans** is on in *Settings → MCP exposure* (off by default):

| Tool | Purpose |
| --- | --- |
| `create_step_plan` | Start a plan from an ask. |
| `list_step_plans` | List plans. |
| `get_step_plan` | Read a plan; optional `wait_seconds` for a status check. |
| `amend_step_plan` | Add a follow-up request as a numbered revision. |
| `edit_step_plan` | Add, update, delete, skip, accept all. |
| `run_step_plan` / `pause_step_plan` | Start or pause an automated run. |

- Inside a conversation the tools are **pinned** to it: any other topic or chat the model names
  is overridden, so it can only create, amend, run or pause its own plans. If the model omits
  the source, the current conversation is used, and the originating user message is attached
  alongside the model's delegated scope.
- The system prompt suggests delegating substantial multi-stage work (interdependent
  deliverables, work needing skills and tools). There is no fixed rule, the choice is the
  model's, and an explicit "don't delegate" is respected.
- A model delegating file work names the skill in the ask; the step's draft loads and runs it,
  and the final output hands over the file's path, not its content.
- The model is told to report a newly started run once and end its turn rather than poll,
  unless you ask it to wait. The Tasks card tracks progress (and refreshes periodically while
  a run is active).

### Approvals in delegated plans

Delegated plans' discussions inherit the source conversation's recorded autonomy for tool
approvals. Rule precedence: discussion rules → source conversation rules → global rules. The
shield pill overrides the policy; **Default** returns to the plan's policy. Plans you start
yourself use the normal chat default. See [Tool approvals](/features/approvals).

### Hand-back

- In Plan, Balanced and Manual modes the source model is woken when you send a completed output.
  A forced provisional output does not wake it.
- Hand-back waits while the source is generating or has a pending approval. If that turn
  already read the exact filed version, it isn't asked again. If the final is superseded,
  relinked or its filed message changes while waiting, the outdated review is skipped.
- The hand-back includes the current final assessment score when available, labelled as a
  model estimate.
- Immediately before an autonomous hand-back, the app re-checks the assessed text, steps,
  assessment and source, and files the message and its version record together; if anything
  changed it stops instead of sending a stale result.
- If an automatic review fails, the plan stays in *Consolidating* with a note.

### Amendments

The source conversation's **current** autonomy decides what runs after an amendment (Plan
waits for **Start the run**, Manual leaves new steps for approval, Balanced/Autonomous continue).
If saving succeeds but starting the run fails, the amendment stays saved and the failure is
reported. Amendment steps are told to inspect current files and prefer a revised copy unless
you ask for an in-place edit. Later requests replace earlier requirements only where they
explicitly change them.

## Assessments

### What is judged

- Each step is judged against **its own instruction**; final-deliverable requirements are not
  due at every intermediate step.
- The overall assessment judges the **original ask and accepted follow-ups**, not an average
  of step scores. Before a final exists it reads only the steps (**Plan readiness**); after,
  it reads the final text against the ask, output instructions and approved artifacts
  (**Final output assessment**).
- An assessment is tied to the work it saw. Changes to the artifact, instruction, ask or an
  accepted follow-up mark it **Out of date** (the overall one as soon as any step changes).
- For factual work the assessor is told to check claims against sources or read-only tools;
  citations and confident wording are not evidence.

### App checks on files

When a deliverable is a file, the app verifies it before the model judges it:

| Check | Cap on failure |
| --- | --- |
| File missing or empty | 25 |
| File unreadable | 40 |
| Slide count differs from the ask ("4 slides"; a cover slide isn't counted) | 60 |

JSON must parse, and a bounded excerpt is included. A file is only read when a tool of the plan
mentioned its path, never in hidden folders. `/ws/<workspace>/<file>` links resolve to that
workspace's file. Failed app checks can't be overridden by the model.

### Evidence and inspector tools

- The assessor receives recent recorded tool responses from the relevant plan discussions: up
  to **12 per discussion, 64 in total**. These are bounded records, not a full audit.
- When the step built files or ran tools, internal inspectors are offered: `list_files`,
  `read_file` (Word, PowerPoint, Excel, HTML and text are converted, long files paged) and
  `read_tool_output` (up to 12,000 characters). They run under the server name
  `step-inspector` and only read files a plan tool mentioned or in the plan's workspace
  folder. File text is treated as data.
- The assessor runs the **same tool loop as a chat** (the step chat's model, tool budget, MCP
  servers, approval rules), with autonomy treated as balanced: read-only tools run; any other
  tool (or one without a read-only hint) is **declined, not parked**, unless an allow rule
  covers it. Manual policy or an **ask** rule declines inspector calls too. MCP exposure does
  not disable the inspectors.
- On its last round tools are withdrawn so it answers. Review input that doesn't fit the
  context fails the assessment rather than being silently truncated. A failed or malformed
  review is reported, never replaced by a tool-less judgment.
- The assessment is an ephemeral review, not a message in the discussion.

### Automatic runs

- In **Autonomous** and **Balanced** runs, a step with a failed app check, a score under
  **60**, or unmet requirements is **redrafted once** with the failures as instructions.
- Each step is checked **before the next one starts**. After the retry, a remaining failed
  check, gap or contradiction, or a score under **50**, pauses the run at that step; resuming
  holds there until a person reviews it. 50–59 passes only with no gaps or contradictions left.
- A failed or out-of-date assessment pauses the run and keeps the draft.
- An existing unfinished or weak draft is preserved for review, not overwritten. Existing
  files of unclear ownership aren't overwritten: the draft picks a fresh path or asks.
- The final output is assessed too. Unmet requirements or contradictions rebuild a freshly
  generated final once (an existing final is preserved). A final that names a missing file, or
  pastes content instead of the built file's path, is rebuilt once with the real paths. If it
  still fails, scores under 50, or its assessment is missing or stale, nothing is filed and
  the run stops for your review.
- Transient model failures (timeout, 5xx) are retried twice with a short pause.

Model assessments can be wrong in both directions: a false gap can stop a run, and a real
defect can pass. Treat them as one tool-capable review, not proof.
