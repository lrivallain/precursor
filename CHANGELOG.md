# Changelog

All notable changes to Precursor are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Precursor uses **CalVer** (`YYYY.M.MICRO`); the version is derived from the
latest git tag (`v<version>`) by hatch-vcs at build time. See
[RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- **Import and export agents and workflows as YAML.** A workflow exports to a
  single, readable file — its steps, its wiring, and every agent those steps use
  — so a pipeline can be committed next to the project it automates or handed to
  someone else; an agent exports on its own the same way. What travels is the
  *definition*: runtime state (status, run history, artifacts, token counters,
  the SDK session handle) is left out, webhook tokens are never exported because
  they're per-install credentials, and a carried schedule arrives **paused** so a
  shared file can't start firing on its new owner. Importing is two-phase, since
  the interesting decision only exists once collisions are known: a preview
  reports them without writing anything, then each colliding agent is resolved as
  **use existing** (reference it untouched), **replace** (overwrite its
  definition in place, so every other workflow using it follows — with the blast
  radius shown up front) or **create new** (keep both). Objects are stamped with
  a stable portable id on first export, so re-importing a file that came from
  this install updates the object it came from rather than guessing from the
  name; a bare name match defaults to the non-destructive choice instead, which
  also makes scripted imports safe. New `/api/transfer` router, and
  `export_id` columns on `agent_sessions` and `workflows`.

- **Create a reusable agent from the step editor.** An Agent step now chooses
  between **Existing agent** (pick one from the Agents section) and **New agent**
  (name it and give it an objective right here) — so building a pipeline no
  longer means breaking off to create its parts first. The agent lands in the
  Agents section on save, editable and pickable by other steps and workflows;
  reopening the step then shows it as a plain reference, because creating is a
  one-time act rather than a mode a step stays in. Backed by
  `WorkflowStepInput.reusable`, which decides whether a step-authored prompt
  mints a real agent or the step's private vessel — omitting it keeps the vessel,
  so existing payloads are unchanged.

### Changed

- **"Inline prompt" is no longer offered on an Agent step.** It produced exactly
  what the **Inline** step type produces — the same hidden vessel, the same
  runtime, differing only in the badge on the board — so the same intent had two
  spellings and the step type disagreed with the toggle beneath it. One-off work
  is now the **Inline** kind, full stop. A **Gate** keeps all three sources,
  since there is no inline gate kind for its one-off check to be. A blank step
  starts as **Inline**, which is where it effectively started before.

### Fixed

- **"Open Settings" from the Agents-off screen now lands on Agents.** The button
  told you to turn the feature on and then dropped you on Appearance, leaving you
  to find the right category yourself. It opens Settings directly on **Agents**,
  where the toggle it just pointed at is.

- **Switching a step's kind or agent source no longer strands its agent.**
  Converting an inline step to an agent-backed one kept the reference to its
  private vessel, which the save then swept as an orphan — leaving a step reading
  "Missing agent". The reference is now dropped whenever the *source* changes,
  and kept when it doesn't, so an unchanged inline step still updates its vessel
  in place and keeps its run history.

- **Step colours now reset when a new run starts.** The strip derived each step's
  state from its *agent's* live status, which survives between runs — so a step
  that finished last time rests at `idle` and lit up green the instant a fresh
  run began, before it had done anything. Step state now comes from the **run
  trace** (what happened to that step *in this run*); a step with no trace yet
  reads `pending`. Scrolling the run picker replays how the strip looked for that
  run, while a live run never paints from a different one. The agent-status
  fallback (used by the gallery, which doesn't load traces) additionally refuses
  to mark anything past the running cursor as done.

- **An approval note now reaches the steps after it.** Approval checkpoints are
  transparent to the data flow (they publish no content, so the *material* still
  comes from the last real producer) — but that also silently dropped the
  reviewer's note, so approving with "translate it into French before sending"
  had no effect on the sending step. Notes are now read back off the run's
  approval traces and injected into every later step as a reviewer directive,
  alongside the content rather than instead of it. Approving with no note injects
  nothing.

- **The agents board uses the app's own tooltips.** Its hover hints were native
  browser `title` attributes, so they appeared after a long OS delay in a system
  bubble that ignored the theme — visibly different from every other hint in the
  app. The KPI tiles, the Auto badge, the narration line, the inbox rows and the
  new workflow chip all use the shared tooltip now.

- **Removed the unreachable agents sidebar list.** The agents section has been a
  card board since the dashboard landed, and the sidebar is force-collapsed (and
  un-expandable) in that mode — so the old `AgentList` sidebar rows could not be
  reached by any route, while still being maintained alongside every agent
  change. Deleted, along with the `agentSlot` plumbing that fed it.

- **No more double border on the in-progress step.** The active step drew a
  border *and* a 2px ring underneath the animated holographic frame; the frame
  now owns the edge alone. An approval step also no longer mislabels itself
  "Missing agent" — it legitimately has no agent, and reads "Human approval".

### Added

- **Workflows can be archived.** Archive hides a workflow from the gallery while
  keeping its definition and run history; the shared **Archive** panel gains a
  Workflows tab to restore or permanently delete it, alongside topics, chats,
  agents and live sessions. The backing endpoints existed but had no UI at all.
  Backed by a new `GET /api/workflows/archived`.

- **An agent shows which workflows use it.** Every agent **card on the agents
  board** carries a workflow chip with the number of pipelines using it. Clicking
  it jumps straight to the workflow when there's only one, and names them on the
  card when there are several — so the link is one click from the board rather
  than buried in settings. The agent settings panel keeps the same count and
  list. Editing a shared agent isn't a blind change any more. Archived workflows
  don't count, and private inline vessels never appear. Backed by
  `GET /api/agents/{id}/workflows` and a `workflow_count` on the agent payload
  (one grouped query, not one request per card).

- **Workflow schedules match topic and agent schedules.** The workflow recurrence
  editor now uses the same control as scheduled topics and agents: an arbitrary
  "every *N* minutes / hours / days" interval, or a **time of day on chosen
  weekdays**, in your local timezone. It previously offered four fixed presets
  (hourly / 6h / daily / weekly) with no weekday selection.

- **A real icon picker for workflows.** Browse a categorised set, search it by
  keyword, or paste **any** emoji (including from the OS picker) — and **No icon**
  is a first-class choice rather than a fallback gear. Clearing one also required
  a backend fix: the update handler tested `if payload.icon is not None`, so a
  null could never remove an icon once set.

- **Settings → Workflows.** A section for the defaults a new workflow and its
  steps start from: whether a step may use **tools**, **skills** and **memory**,
  and the **stall watchdog** a new workflow carries. All still overridable per
  workflow/step. A default of *on* leaves a step's override unset (inherit its
  agent); a default of *off* is written explicitly, because "inherit" would
  quietly turn it back on.

- **Workflows on the home page.** The launcher grid now offers Workflows
  alongside Topics, Chats, Live, Agents and Files.

- **Any prompt written inside a step stays private to it — including a gate's.**
  A step's agent is a hidden vessel whenever its prompt was authored in the step,
  so a one-off quality check ("is *this* joke safe for kids?") no longer leaves a
  permanent agent behind any more than a one-off task does. The choice is now
  **Existing agent** (reuse one from the Agents section) vs **Inline prompt**
  (write it here), offered for both Agent and Gate steps; the old "New agent"
  silently created a reusable agent instead. Referenced agents are never touched.

- **Inline steps — one-off work that isn't an agent.** A step can now be
  **Inline**: its instructions live with the step instead of in a reusable agent,
  so a pipeline stops leaving a trail of single-purpose agents in your Agents
  list, and the work is deleted along with the step (or the workflow). In a run
  it behaves exactly like a Task step — it produces content, hands it downstream,
  and takes the same context, capability and failure settings. Under the hood the
  step keeps a private hidden agent as its execution vessel (the runtime is
  agent-keyed); editing the step updates that vessel in place so its run history
  survives, and it's swept when no step owns it. It stays listed in
  **needs-attention** by design, so an inline step blocked on a tool approval
  can't wedge a workflow invisibly. Backed by `WorkflowStep.kind = "inline"` and
  `AgentSession.inline` (migration `b6e1f70a3c48`).

- **Steps are authored on the workflow board, not in a dialog.** Hit **Edit
  steps** and the horizontal strip you already know becomes editable — same
  layout, now authorable. **Drag a card** anywhere to reorder, **click a card** for that step's own
  settings modal (kind, agent — existing or created inline — instructions,
  context sourcing, capability toggles, failure policy), and the **+ between
  cards** inserts a step at that position, drawing the seam it will be spliced
  into as you hover. Keeping the horizontal layout is
  deliberate: the strip is the picture of the pipeline and shouldn't change shape
  just because you're editing it. The **New/Edit workflow** dialog keeps only the
  workflow's identity and run-wide settings, and a new workflow starts empty for
  the board to fill in. Editing is an explicit mode with an explicit save and is
  **refused while a run is in flight**: saving replaces the whole step list
  server-side and the run cursor is `ON DELETE SET NULL`, so a mid-run write
  would strand the coordinator. Backed by a new `WorkflowStepEditModal` that owns
  the draft model (`draftsFromWorkflow` / `draftsToPayload`), leaving
  `WorkflowBuilder` a third of its former size.

- **Workflow notifications.** A background pipeline can now reach you: a run
  raises a notification when it **finishes**, **fails**, or — most importantly —
  **parks on a human approval**, which is shown even when the app is focused
  because the run is *blocked* until you answer. Step-to-step progress stays
  silent and each transition notifies once. The `workflow.changed` SSE event now
  carries the run status and workflow name (the event bus whitelists payload
  keys and had been dropping `workflow_id` entirely).

- **Per-step context sourcing.** A step no longer has to inherit everything: pick
  **Previous step** (the default hand-off plus the artifact board), **Pick steps**
  to name exactly which earlier steps feed it, or **None** to run on its own
  objective alone. The run brief, reviewer directives and step instructions are
  always delivered — this governs the *material*, not the intent. Backed by
  `WorkflowStep.context_mode` / `context_sources`.

- **Per-step capability toggles (tools, skills, memory).** Agents gain
  `use_mcp` / `use_skills` / `use_memory`, and each workflow step can override
  them tri-state (auto / on / off). Tool schemas are a large fixed context cost
  paid every turn, so a step that only rewrites a paragraph needn't carry the
  whole MCP catalogue; a pure transform step is usually better off not consulting
  long-term memory. Flipping one rebuilds the agent's session on its next run.

- **A workflow-wide Assistant role.** Pick a role on the workflow and every
  step's agent adopts it for the run — applied at launch rather than stamped onto
  the shared agent rows, so the same agent can be formal in one pipeline and
  blunt in another. Backed by `Workflow.role_id` (migration `a9d4e7f21c60`).

- **Editing a step's agent without leaving the workflow.** The step modal now
  edits the agent's objective in place and toggles its capabilities, instead of
  bouncing out to the Agents cockpit to change one line. Saving primes the change
  for the step's next run; it never launches a turn.


- **Human approval checkpoints, with a reject policy.** A step can now be an
  **approval**: the run *parks* on it — no agent runs, nothing is spent — until a
  human decides. Where a gate is an agent judging the work, this is you judging
  it, so you can put a checkpoint in front of anything irreversible (sending the
  email, publishing, filing). Approving resumes the pipeline — and the note you
  leave is **forwarded to every later step** as a reviewer directive, so you can
  course-correct mid-run ("translate it into French before sending") without
  editing the workflow. Rejecting follows the checkpoint's **reject policy** — **send back** (loop an earlier step with
  your feedback injected, reusing the gate machinery and `max loops` cap),
  **stop the run** (recorded as `cancelled`, not `failed` — nothing broke, you
  decided), or **skip ahead**. The decision panel always also offers *Stop the
  run*, so the policy is a default rather than a cage. Backed by the `approval`
  step kind, `WorkflowStep.on_reject` (migration `f3b8d1c05a92`), an
  `awaiting_approval` workflow/run status, and
  `POST /api/workflows/{id}/approve` \| `/reject`. See
  [features/workflows](website/features/workflows.md).

- **Per-step instructions — one agent, a different job in each workflow.** A step
  can carry its own mandate, layered on the referenced agent's standing objective
  and taking precedence where they differ, so a single `Summariser` row can be
  the terse-bullets stage in one pipeline and the exec-brief stage in another
  without cloning it. Instructions land at the end of the kickoff preamble where
  they carry the most weight; on an approval step they're what the reviewer sees.

- **Per-step failure policy + stall watchdog.** A failed step no longer always
  kills the run: each step chooses **stop run** (the default), **retry** up to
  *N* times (with the failure reason injected so the retry isn't a blind repeat,
  each attempt appending its own trace row), or **carry on** for steps whose
  output is optional. Retry budgets reset per run. Separately, a workflow can set
  a **stall watchdog** (minutes; `0` = off): a step running past it is declared
  stuck, its agent cancelled, and it's put through that same failure policy — so
  an unattended pipeline can't sit in `running` forever behind a hung turn.
  Backed by `WorkflowStep.on_error`/`max_retries`/`retry_count`,
  `Workflow.step_timeout_seconds` (migration `e7c2f4a9b8d1`) and a scheduler
  sweep.

- **Cost roll-up per step and per run.** Every step attempt records the token
  spend of its turn and the run accumulates the total, shown in the run header
  and on each trace row — so a gate that looped four times reveals what each pass
  cost. Turns "did it work?" into "was it worth it?".

- **Per-run brief — point one reusable workflow at a different subject each
  run.** Starting a workflow now takes an optional free-text **brief** ("analyse
  `/data/q3-sales.csv`, EMEA only"), so the definition stays generic
  (`analyse → review → report`) while the run carries the subject. The **Run**
  button gains a caret that opens a brief composer (`⌘↵` to run); leave it empty
  and the pipeline runs fully autonomously exactly as before. The brief leads
  **every** step's kickoff preamble — not just the first — so a gate can judge
  the work against what was actually asked, a loop-back re-drives the producer
  with the brief still attached, and a final step knows which file it was about.
  It's stored on the run (new `workflow_runs.input` column, migration
  `d5e9b0c1a2f3`) and shown on the run header, so browsing back through history
  explains itself. `POST /api/workflows/{id}/run` accepts an optional
  `{ "input": … }` body and a webhook's posted payload becomes the run's brief;
  scheduled runs stay brief-free by design. See
  [features/workflows](website/features/workflows.md).

- **Workflow run history + inspectable per-step trace.** Every workflow
  execution is now persisted as a `WorkflowRun` with an append-only
  `WorkflowRunStep` trace — one row per step **attempt**, capturing what the step
  *received* (`input_context`) and *produced* (`output_summary`), plus gate
  verdicts and per-attempt duration. Gate loop-backs append a fresh attempt row
  rather than overwriting the prior one, so retries read as distinct entries.
  The workflow detail page gains a **run-progress header** (status, trigger,
  live current step, elapsed, % complete), a **run picker** to scroll back
  through past executions, and a collapsible **Run trace** timeline; the step
  modal now shows the **Input received** by a step and its full **Run history**
  across attempts. Backed by new `workflow_runs` / `workflow_run_steps` tables
  (migration `c3f7a1b2d4e8`), engine trace-recording in
  `services/agents/workflow.py`, and `GET /api/workflows/{id}/runs`. See
  [features/workflows](website/features/workflows.md). The run **result panel**
  now renders Markdown (bold, lists, rules) instead of raw text, the running
  step wears an animated holographic border mirroring the composer's
  thinking-state, and the run-trace rail is aligned dead-centre through its
  step dots. Runs are **deep-linkable** — `/workflows/<id>/run/<n>` pins a
  specific run and `/workflows/<id>/run/latest` follows the live one, with the
  URL updating as you scroll the run picker and staying put on a pinned run when
  new runs fire. Turns an agent runs **as a workflow step** no longer mark it
  **unread** in the Agents section — that badge is reserved for genuinely
  autonomous runs (started manually or by schedule), so the coordinator driving a
  step doesn't spam agent badges.

- **Workflow steps read the whole run's artifacts (shared blackboard).** Beyond
  the immediate hand-off (the previous step's full answer + artifacts), a step
  now also inherits the **artifacts published by every earlier step** in the
  run, labelled by step and oldest-first, as a reference-material section in its
  kickoff preamble. A reviewer three stages down sees the first step's research
  inventory without the middle steps having to re-forward it; steps that
  published nothing are skipped and gates never appear on the board. Backed by
  `collect_prior_artifacts` / `_earlier_content_agent_ids` in
  `services/agents/workflow.py`. See
  [features/workflows](website/features/workflows.md).

- **Workflow gates with conditional loop-back — simple prompt chaining with a
  quality gate.** A workflow step can now be a **gate**: it votes `PASS`/`FAIL`
  on the work so far and, on failure, re-drives an earlier step (its **on-fail
  target**) with the critique injected, so a bare chain like *tell a story → is
  it kid-safe? → note the story* loops until it passes — no special prompting
  needed. A step now feeds the next its **full answer** (not just the folded
  `OBJECTIVE_COMPLETE` summary), so results are shared implicitly. A gate is
  **transparent to the data flow** — the step after it receives the last real
  producer's output (the material the gate validated), not the gate's terse
  `PASS: …` verdict, so *tell a joke → is it kid-safe? → note the joke* actually
  notes the joke. Retries are
  bounded by a workflow-level **max loops** cap (default 3); exceeding it stops
  the run in `failed`. Backed by new `WorkflowStep.kind`/`on_fail_position`/
  `attempt_count` and `Workflow.max_loops` columns (migration
  `a7c93e21f4d8`), gate verdict parsing in `services/agents/workflow.py`, and
  Task/Gate controls in the workflow builder. See
  [features/workflows](website/features/workflows.md).
  **Workflows** mode (opt-in, off by default) turns a named sequence of
  independent agents into a `research → draft → review` pipeline where the
  *workflow* owns the chaining, not agent-to-agent `depends-on` links. Build one
  from existing agents or create steps inline; run/pause/resume/cancel it from a
  lifecycle bar; drill into any step's agent run in a modal while the pipeline
  stays live in the background. Each step is fed **only** the previous step's
  summary + artifacts, and a re-run clears each step's artifacts first. A workflow
  can be **parked** and triggered manually, on a **schedule** (hourly/6h/daily/
  weekly), or by **webhook** (`POST /api/workflows/hooks/{token}`). Backed by new
  `Workflow`/`WorkflowStep` models, a `/api/workflows` router, the
  `services/agents/workflow.py` coordinator, and a `workflow.changed` SSE event.
  See [features/workflows](website/features/workflows.md).
- **Park an agent for a trigger, then start it on demand.** The composer now has
  a **Create parked (don't run yet)** toggle: leave it on to launch immediately
  (the old behaviour), or turn it off to arm the agent in a new **`waiting`**
  lifecycle state. A parked agent stays idle until something triggers it — its
  webhook firing, the new **Start now** button on its detail view, or a workflow
  step reaching it. A parked agent is never auto-swept; only an explicit trigger
  launches it. Backed by a `start` flag on
  `POST /api/agents` and a new `POST /api/agents/{id}/start` endpoint. See
  `_spawn_agent`/`start_agent` in `routers/agents.py`.
- **Re-running an agent — or clearing its context — starts from a clean
  blackboard.** A fresh objective run (manual restart, retry, or webhook
  re-trigger) **and** clearing an agent's
  context with `/clear` now wipe the artifacts a previous run published, so the
  new turn's deliverables replace the stale ones instead of piling up beside
  them. Conversational follow-ups (`/send`) keep their artifacts. See
  `_clear_artifacts` (called from `start_task` and `clear_session`) in
  `manager.py`.
- **Multi-line ARTIFACT block form so deliverables land whole.** Substantial
  outputs (a research inventory, a draft, a review) can now be published as a
  block — `ARTIFACT: <title>` with no pipe, then the full Markdown body on the
  following lines, closed by an `END_ARTIFACT` line (or the next directive / end
  of message). This fixes the case where a model put its real deliverable across
  many lines and only a heading after the inline `|`, so the artifact captured
  just the heading. The single-line `ARTIFACT: <title> | <body>` form still works
  for short values, and a trailing `PROGRESS`/`OBJECTIVE_COMPLETE` line a model
  glued onto the body is now stripped off. See `_extract_artifacts` in
  `manager.py`.
- **Autonomy cadence promoted to the durable system layer.** The behavioral
  cadence that makes the dashboard useful is now baked into the autonomous
  agent's system preamble (`_AUTONOMY_PROTOCOL`) and per-turn nudge, so it
  applies to *every* run rather than relying on each mission prompt to restate
  it: narrate one short plain sentence before each action (feeding the live
  dashboard narration), emit `PROGRESS` several times across the run (early /
  middle / late, not only at the end), publish an `ARTIFACT` as each phase or
  finding lands, and close with a **2–3 sentence** `OBJECTIVE_COMPLETE` (up from
  a one-liner — it now reads better folded into the answer bubble). Task-specific
  specifics (named phases, exact checkpoint percentages like 15/40/65/90%) stay
  in the mission prompt: the durable layer sets the floor, the task sets the
  shape.
- **Artifact deliverables render as real Markdown, not run-on lines.** A single
  `ARTIFACT:` directive is one physical line, so a model can't press Enter inside
  it — a numbered list or multi-paragraph payload used to collapse into one
  inline paragraph. The autonomy prompt now tells the model to write `<content>`
  as Markdown using `\n` for line breaks, the directive parser unescapes those
  `\n`/`\t` sequences, and — as a safety net — a numbered list packed onto one
  line (`1. … 2. … 3. …`) is broken back onto separate lines (only a strictly
  sequential run, so decimals/versions/prices are left alone). The frontend
  mirrors the same normalization at render time, so **already-persisted**
  artifacts also render correctly without a re-run.
- **Objective-complete folded into the answer bubble.** A terminal
  `OBJECTIVE_COMPLETE` turn no longer renders as a separate emerald milestone
  node beneath an otherwise-hollow answer bubble. The completion is now folded
  **into the final answer bubble itself** — an *Objective complete* badge on the
  bubble, and (when the turn's prose was entirely control directives) the summary
  becomes that bubble's body — so the completion and the answer are one and the
  same, not two adjacent nodes echoing each other. Progress heartbeats still drop
  their own violet milestone pills on the spine.
- **Deliverables inline in the discussion flow — as a single, non-duplicated
  answer.** An agent's published artifacts now render at the foot of the
  conversation **unboxed, in the normal discussion background**: a horizontal
  rule slips each one off from the streamed prose, a quiet title row labels it,
  then the body renders as plain **Markdown** (Markdown passes through, JSON in a
  fenced block, `link` as a real anchor), so it reads as the agent's answer to
  your request. Copy content / copy link / open raw surface on hover. To stop the
  same content repeating as prose, completion, *and* deliverable, the `ARTIFACT:`
  payload no longer renders inline in the message body, and the section prefers
  the model's explicit `ARTIFACT:` outputs — falling back to the auto-captured
  completion summary only when nothing explicit was published (that summary is
  already shown in the *Objective complete* answer bubble). The insights-sidebar
  list stays as a compact index a workflow can feed into a later step's kickoff
  context — so the deliverable is consumable in the discussion *and* still travels
  the blackboard.
- **Addressable agent artifacts (permalinks + raw endpoint).** Published
  blackboard artifacts are now openable on their own. The insights-sidebar list
  entries are clickable: they open a viewer that renders the artifact by kind
  (Markdown, pretty-printed JSON, plain text, or a clickable link) with **Copy
  content**, **Copy link**, and **Open raw** actions. Two new routes back this —
  `GET /api/agents/{id}/artifacts/{artifactId}` (single artifact) and
  `GET /api/agents/{id}/artifacts/{artifactId}/raw` (raw body with a
  kind-appropriate content-type; a `link` artifact `307`-redirects to its URL).
  The permalink form `/agents/{id}?artifact={artifactId}` reopens the agent with
  the artifact auto-expanded.
- **Live agent narration in the dashboard.** While an agent has a turn in
  flight, `live_activity` now distils its own natural-language commentary (the
  first meaningful prose line of the streaming assistant message, stripped of
  control directives and markdown) into `AgentSessionRead.active_narration`. The
  dashboard card renders it under the active-tool chip (or as a standalone chip
  when no tool is running) and the sidebar rail surfaces it as the row label +
  tooltip, so a backgrounded agent reads as "what it's doing now" in plain
  language rather than a bare tool name. Mirrors to the frontend types.
- **A multi-agent orchestrator (budgets, triggers, blueprints, blackboard,
  unified inbox).** Agents mode graduates from independent runs to a coordinated
  **fleet** you watch from one dashboard. A **concurrency
  governor** (`agents_max_concurrent`, default 3) caps how many run at once; a
  per-agent **token budget** parks an agent in the inbox for approval instead of
  overspending; and **retry/backoff** (`max_retries` + `agents_retry_backoff_seconds`)
  auto-recovers a failed run with exponential delay before giving up. **Blueprints**
  (`/api/agents/blueprints`) save a reusable task + governance profile you stamp
  into fresh agents; a **shared-artifact blackboard** (`/api/agents/{id}/artifacts`)
  lets completed runs publish results (deduped) for a workflow or a watcher to read;
  **webhook triggers** (`/api/agents/{id}/triggers` + `POST /api/agents/hooks/{token}`)
  re-run an agent from an external event. Two new aggregate surfaces back the
  cockpit: `GET /api/agents/metrics` (fleet rollup — counts, running/queued,
  token + budget totals) and `GET /api/agents/inbox` (everything waiting on you —
  approvals, raised questions, budget parks — in one list). The dashboard grows a
  **unified inbox strip** and a **concurrency/token rollup**; each run's insights
  sidebar gains an **artifacts panel** and **webhook**
  management; the agent settings drawer gains **token budget** + **max retries**;
  and Settings gains a **blueprints** manager. New `AgentSession` columns
  (`token_budget`, `max_retries`, `retry_count`, `retry_at`, `total_input_tokens`,
  `total_output_tokens`) and three new tables (`agent_triggers`,
  `agent_artifacts`, `agent_blueprints`) land in one Alembic
  migration; all new read/create schemas mirror to the frontend types.
- **Autonomous agent missions (opt-in).** An agent can now be started in
  **autonomous** mode, turning its task prompt into a **durable objective** it
  pursues on its own: a **goal loop** inspects each idle turn for a control
  directive and either finishes, blocks for input, or **continues itself** to the
  next step, so a multi-step mission lands as a **single** result in the linked
  topic/chat instead of a burst of intermediate turns. Agents self-report
  **progress** (`PROGRESS: <0-100> | label` → a progress bar on the card, the
  mission strip, and the open run), raise a question via `NEED_INPUT:` to enter a
  new **Needs input** (`blocked`) state that floats to the top of the dashboard
  next to approvals (your reply resumes the mission), and finish with
  `OBJECTIVE_COMPLETE: <summary>`. A per-agent **step budget** (default 12) plus a
  **stall guard** cap the loop so it can't spin forever; a blocked agent's
  scheduled re-runs are skipped so a cadence never discards its question. The mode
  is **off by default** — plain agents rest at Idle after each turn exactly as
  before — and is toggled (with a step-budget input) on the start-agent form. New
  persisted fields on `AgentSessionRead` (`autonomy_enabled`, `max_steps`,
  `step_count`, `progress`, `progress_label`, `blocked_question`) mirror to the
  frontend types.
- **Per-agent approval policy.** The approval policy gating an agent's actions is
  now **selectable per agent** instead of only globally: choose *Inherit global
  default* / `manual` / `balanced` / `autonomous` on the start-agent form or from
  the agent's settings drawer. A nullable `approval_policy` column on
  `AgentSession` (`NULL` = inherit) is resolved **per turn**, so switching it
  needs no session rebuild, and it wins over the Settings-wide default only when
  set. Exposed on `AgentSessionRead` / `AgentSessionCreate` / `AgentUpdateRequest`
  and mirrored to the frontend types.
- **An agent control-tower dashboard.** Agents mode now opens on a fleet
  **dashboard** instead of a single run: a row of **KPI stat tiles** (**Need
  you** / **Working** / **Idle · done** / **Scheduled**) — the *Need you* tile
  glowing amber and the *Working* tile spinning while live — over **urgency
  swimlanes** of monitor cards, one per agent, each with a live status medallion,
  current tool, next scheduled run, unread count, linked topic/chat, and model.
  Cards are **urgency-sorted** by an attention router — an agent blocked on you
  floats to the top, then interrupted/failed, then live work, then quiet states —
  and grouped into **Needs you / Working / Idle · done** lanes with a hover-lift
  and a status-colored rail. A shared **status medallion** (pulses while working,
  wears an amber ring when it needs you, grows a fan-out cluster badge for
  parallel tool calls) renders identically on the dashboard and the command
  palette. While an agent works, its **current tool** and a `×N parallel` count
  are shown live, derived from a new in-process `live_activity()` snapshot exposed
  via three fields on `AgentSessionRead` (`active_tool`, `active_tool_count`,
  `pending_permission`).
- **An out-of-band "agent needs you" signal.** When a background or scheduled
  agent pauses for approval, a browser notification now fires **regardless of
  window focus** and deep-links to that agent, the **tab title** grows a 🔔 and a
  waiting count, and the **⌘K palette** lists the agents that need attention
  first — so you can leave agents running in the background and be pulled back
  only when one is genuinely blocked. All of it respects the existing
  notifications toggle.
- **A lapsed idle MCP sign-in surfaces itself, instead of stalling your next
  request.** WorkIQ / [Agent 365](website/features/mcp.md#agent-365-workiq-teams-and-workiq-user)
  credentials that had gone idle (per the keep-alive back-off) used to die
  quietly — you'd only find out when a simple request stalled for seconds on a
  doomed OAuth handshake before the sign-in banner finally appeared, or by
  manually opening **Settings → MCP**. Now the keep-alive **probes an idle token
  once it has genuinely expired** and raises the `McpAuthBanner` proactively if
  its refresh token is dead (a still-refreshable session recovers silently), and
  a detected lapse records a verdict so the **first turn** that touches the
  server **fast-fails straight to the prompt** rather than paying the multi-second
  connect. New `workiq_keepalive_surface_idle_lapse` knob (default `true`,
  `PRECURSOR_WORKIQ_KEEPALIVE_SURFACE_IDLE_LAPSE`) opts back into fully silent
  idle credentials.
- **Per-collection default role.** A [collection](website/features/collections.md)
  can now nominate a default **Assistant Role** — new topics created in it start
  with that persona unless the caller picks another one, so a whole collection
  can lean into one role without setting it per topic. Set it from **Settings →
  Collections**; a new nullable `default_role_id` on the collection (schema, API,
  and a migration) drives it, and deleting a role reverts any collection that
  pointed at it back to the built-in default.
- **Copilot AI-credit usage in the persona menu.** When you're connected with a
  GitHub account, opening the sidebar persona menu now shows your Copilot **AI
  credits** — a progress bar with the percentage used and the next reset date.
  It's fed by a new `GET /api/me/copilot` endpoint that reads the account's
  `premium_interactions` quota from GitHub with your own token and is fetched
  lazily on menu open (so `/api/me` stays fast). The bar warms amber past 75%
  and red past 90%; unlimited plans show an **Unlimited** badge, and accounts
  with no Copilot seat simply omit the section.
- **Choose the Playwright browser from Settings.** The MCP tab now has a
  **Playwright browser** selector (`msedge` by default, plus `chromium`, `chrome`,
  `firefox`, `webkit`, and `Default`) backed by a DB-overridable
  `playwright_browser` setting that wins over the `PRECURSOR_PLAYWRIGHT_BROWSER`
  env default and applies live — the warm worker is retired so the next call
  relaunches with the new channel. The new **Default** choice **omits `--browser`
  entirely**, the escape hatch for environments whose resolved `@playwright/mcp`
  predates the flag and fails to start with `error: unknown option '--browser'`.
- **Playwright `--browser` support is now auto-detected.** On startup Precursor
  probes the resolved `@playwright/mcp` once and **omits `--browser` automatically**
  when the build predates the flag (e.g. behind a stale registry mirror), so the
  server launches instead of failing with `error: unknown option '--browser'` — no
  configuration needed. The **Default** selector remains as an explicit override.
- **Kanban issue comments now show when they were posted.** The issue/PR
  preview modal renders each comment's timestamp (localized medium date + short
  time) next to the author, with an *(edited)* hint when the comment was updated
  after posting. The GitHub `IssueComment` schema and its TypeScript mirror now
  carry `created_at` alongside `updated_at`.

### Changed

- **An approval step's run detail reads like any other step's.** Opening one used
  to show a red error — "no agent runs for it" — and nothing else, hiding the
  brief, the input and the decision that *were* recorded. It now shows the same
  anatomy as every other kind: what the reviewer was asked, the **Decision**
  (verdict + note), the input it received and the full attempt history, with the
  no-agent fact stated as a neutral note rather than a failure.
- **The run-step modal is read-only.** Opening a step from a run is for
  inspecting what happened, so it no longer offers objective editing, capability
  toggles or a "nudge this step" composer — authoring lives in *Edit steps* on
  the board, and a past run shouldn't be mutable from the record of it. Its
  **Open in agents** link (and the one in the run trace) now appears only for a
  *reusable* agent: an inline step's vessel isn't listed in the Agents section
  and an approval step has no agent at all. Backed by a new `agent_inline` flag
  on the run-step trace.
- **Removing a webhook lives on the webhook control**, not buried in the
  schedule editor — revoking a token has nothing to do with recurrence. It now
  confirms first, since the URL stops working immediately.
- **Step authoring reads more clearly.** The step modal asks for the **kind**
  first (**Agent** / Inline / Gate / Approval) and only then, for an Agent step,
  whether it reuses an **existing agent** or creates a **new** one — the old
  order asked how before what. "Task" is now **Agent**, since that is exactly
  what distinguishes it: it is backed by a real agent from the Agents section.
- **One prose field per step.** The separate step-instructions box now appears
  only where it adds something — a step reusing an *existing* agent, where it
  customises a prompt written elsewhere (and says so), plus an Approval step,
  where it is the only description. An Inline step, or an Agent step authored on
  the spot, already states its job in its own field.
- **Dragging a step shows where it will land**, as a vertical seam between two
  cards — the same signal the `+` uses — instead of highlighting whichever card
  sits under the cursor, which read as "replace this one".
- The workflow settings dialog no longer carries a note pointing at *Edit steps*,
  and the **Expand sidebar** control is hidden on the Workflows page as it
  already is on Agents.

### Fixed

- **A workflow step no longer stalls asking for clarification.** A task step
  whose objective was slightly under-specified (e.g. *"note this joke 0–10"*)
  could emit `NEED_INPUT` / present a menu of options instead of acting — which
  parked the step **Blocked** and paused the whole run, since a workflow runs
  unattended with no human to answer. Every non-gate step's kickoff now carries a
  strict autonomy directive: complete the objective directly on the given input,
  never ask for clarification or emit `NEED_INPUT`, and if a detail is
  underspecified pick the most reasonable interpretation and produce the
  deliverable anyway. Backed by `_TASK_PREAMBLE` in
  `services/agents/workflow.py`. See
  [features/workflows](website/features/workflows.md).

- **A completed step now shows its deliverable, not the terse completion
  reason.** When an autonomous step wrote its output as prose and then ended with
  `OBJECTIVE_COMPLETE: <reason>` (a *meta* description like "Told the user a
  joke"), the step displayed — and auto-captured as its **"Result" artifact** —
  the reason instead of the actual deliverable (the joke), even though the
  downstream hand-off correctly received the real prose. The completion branch
  now prefers the message's prose body for the displayed `result_summary` and the
  Result artifact, falling back to the reason only when the final turn was
  directives-only (a bare `OBJECTIVE_COMPLETE` with no body). See
  [features/workflows](website/features/workflows.md).

- **Control directives no longer leak into a step's displayed result.** A gate
  stored its raw `OBJECTIVE_COMPLETE: PASS: …` verdict as the step result *and*
  auto-captured it as a shared **"Result" artifact**, so directive syntax showed
  up as a deliverable and polluted the blackboard. A gate is now normalised to a
  plain `Passed — <reason>` / `Rejected — <reason>` verdict and emits **no
  artifact** (a gate judges, it doesn't produce). More generally, control tokens
  (`OBJECTIVE_COMPLETE`, `ARTIFACT`, `PROGRESS`, `NEED_INPUT`, …) are now scrubbed
  from every step's *displayed* `result_summary`; the raw turn is still retained
  internally for directive parsing, content forwarding, and gate verdicts. See
  [features/workflows](website/features/workflows.md).


  lifecycle-directive parser matched `NEED_INPUT:` / `OBJECTIVE_COMPLETE:` /
  `PROGRESS:` **anywhere** in an assistant message, so an agent explaining "I emit
  **NEED_INPUT:** to the dashboard when blocked" tripped the marker mid-prose,
  captured the trailing `**` and surfaced a garbled phantom question (`** to your
  dashboard when blocked.`) that halted the run/workflow. Directives are now only
  recognised at the **start of a line**, and the closing `**` of a bolded
  `**LABEL:**` is eaten so it never leaks into the captured value. Mirrored in the
  backend parser (`parse_agent_directives` in `services/agents/manager.py`) and the
  frontend (`lib/directives.ts`).
- **A parked agent no longer hides the entire agent list.** The `waiting`
  lifecycle state (parked agents) was added to the DB and frontend but omitted
  from the `AgentSessionRead.status` literal, so one waiting agent raised a
  Pydantic `literal_error` for the whole `GET /api/agents` response — the list
  500'd and the UI fell back to the empty-state start wizard, making every agent
  look gone. `waiting` is now part of the `AgentStatus` schema literal (already
  present in the TS `AgentStatus` type). See `schemas/agent.py`.
- **The agent insights sidebar now refreshes live.** The per-agent orchestration
  panel (shared artifacts + triggers) fetched once when the agent was selected and
  never again, so mid-mission `ARTIFACT:` outputs and the completion result only
  appeared after a manual reload. It now subscribes to the `agent.changed` bus —
  mirroring the in-chat deliverables — so the right bar updates the moment new
  content is published.
- **Autonomous agents no longer randomly hit "Permission denied" on every tool
  call.** When an agent's detail page was open as the agent started, the timeline
  poll (`GET /api/agents/{id}/events`) and `start_task` could each call
  `_ensure_live` before either cached the session, firing **two** `session.create`
  requests for the same `copilot_session_id`. The duplicate create left the CLI's
  permission responder mis-wired, so every tool call — including built-in `rg`/shell
  — was denied non-interactively with *"Permission denied and could not request
  permission from user"*, even under the `autonomous` policy, and the run parked
  itself with a spurious `NEED_INPUT`. `_ensure_live` now serialises its
  check-then-create under a per-agent lock, so concurrent callers reuse the single
  session. No API or schema change.

### Changed For autonomous agents, the control-directive markers (`NEED_INPUT:`,
  `PROGRESS:`, `ARTIFACT:`, `OBJECTIVE_COMPLETE:`) are **stripped from the rendered
  message body** — they were previously persisted verbatim and left the one line a
  human must act on buried in prose. The raised question is re-surfaced as its own
  amber **"Needs your input"** callout inside the message (rendered as markdown,
  so emphasis shows), and both that callout and the top blocked banner gain an
  **Answer** button that scrolls to and focuses the reply composer. `PROGRESS:`
  heartbeats and the terminal `OBJECTIVE_COMPLETE:` are additionally re-drawn as
  compact, iconified **milestone nodes on the transcript spine** (violet
  percentage pills capped by an emerald *Objective complete* node), and
  `ARTIFACT:` publications render inline as teal **Published artifact** cards
  (collapsible, mirroring the sidebar blackboard), so a mission's trajectory and
  its named outputs read in the discussion instead of as raw directive lines.
  Pure frontend (`frontend/src/lib/directives.ts` mirrors the backend directive
  parser); no API or schema change.
- **Editing an agent saves without running it.** Changing an agent's objective,
  role, or governance in the settings drawer used to **auto-replay** the task the
  moment you saved. **Save** is now save-only — a changed objective/role *primes*
  the new instructions (the cached SDK session is dropped) but no turn is
  launched. A new **Save & run** button combines both: it persists the edits and
  then starts the objective (clearing the prior run's artifacts first), and is
  disabled while the agent is already active. `PATCH /api/agents/{id}` no longer
  enqueues `restart_with_task`; the frontend runs via the existing
  `POST /api/agents/{id}/start`.
- **Autonomous agents no longer emit user-facing "suggest" follow-ups.** The
  trailing follow-up-chip instruction is now injected only for **plain** agents
  (a human converses with them turn-by-turn); an **autonomous** agent drives
  itself through the control directives and runs unattended, so soliciting
  next-step suggestions there only spent tokens and pulled against both the
  "keep going, don't ask" autonomy contract and the base CLI prompt's
  "don't offer to continue" tone rule. Backend-only preamble tweak in
  `_system_preamble`; no API or schema change.
- **The autonomy protocol now explicitly outranks the base "end tersely" tone
  rule.** The captured GitHub Copilot CLI base prompt instructs the model to end
  a turn without a recap, summary, or status — which pulls directly against the
  control lines (`PROGRESS:` / `OBJECTIVE_COMPLETE:`) an autonomous agent must
  emit for the system to track its mission. `_AUTONOMY_PROTOCOL` now states that
  those control lines are required output and take precedence, reconciling the
  conflict and hardening directive reliability. Backend-only injection tweak; no
  API or schema change.
- **Agents mode drops the parallel session list for a dashboard-first
  navigation.** Opening Agents no longer shows a left list of agent sessions
  next to an open agent (which made agents feel like "alternative topics").
  The sidebar collapses to a **slim icon rail** used only to switch sections,
  and the **fleet dashboard is the home** for monitoring existing agents. Open a
  single agent and its header grows an **← All agents** back button (re-clicking
  the **Agents** rail icon does the same) that returns to the dashboard. Per-agent
  management — rename (double-click the title), **archive**, stop, and delete —
  now lives in the open agent's header, so no capability is lost with the list
  gone.

- **The GitHub Models provider is retired and no longer offered.** GitHub has
  retired the service; `https://models.github.ai/catalog/models` now answers
  `410 Gone`, so selecting the provider only ever produced a raw HTTP error
  where the model list should be. Providers can now carry a `retired` reason in
  the registry, which is the single source of truth for both the settings notice
  and the API: `GET /api/llm/providers` hides retired providers (unless one is
  still selected, in which case it is labelled *(retired)*), and
  `GET /api/llm/models` answers `502` with that explanation instead of leaking
  the upstream `410`. Existing configurations are **not** silently rewritten —
  the provider stays selectable-but-flagged so the switch to **GitHub Copilot**,
  which authenticates with the same GitHub token, is a deliberate choice.

- **Copilot's intermittent model refusals are ridden out instead of surfaced.**
  Only part of Copilot's fleet serves every model, so an identical request
  alternates between `200` and `400 model_not_available_for_integrator` at
  roughly a coin-flip — measured at 3/8 to 5/8 success for `MAI-Code-1-Flash`
  and `gemini-3.5-flash`. Despite the 4xx this says nothing durable about the
  model, so Precursor now retries the rejection while opening the stream (up to
  5 attempts, short backoff), which takes both models from a coin-flip to 6/6 in
  end-to-end runs. The retry is safe because the refusal happens *before* any
  token is streamed, and it is scoped to this one error code — every other 4xx
  still surfaces immediately. If it does keep failing, the message now says the
  rejection is intermittent and worth retrying rather than claiming the model is
  permanently out of reach.

- **`GET /api/reminders/{container}/{id}` returns `200` with a `null` body when
  no reminder is set**, instead of `404`. The conversation panel reads this
  endpoint on every topic/chat open, so the far more common "no reminder" case
  was logging a red `404 (Not Found)` in the browser console on nearly every
  navigation — even though the client already handled it. Unknown container
  kinds are still a genuine `404`.

- **Lockfiles are now resolved by CI, not by contributors**: many managed
  devices route uv and npm through a corporate package mirror, and re-resolving
  there doesn't merely relabel URLs — npm integrity comes back as `sha1` instead
  of `sha512`, and uv drops the `size`/`upload-time` provenance. Because that
  reads as an innocuous URL diff in review, it had already reached the
  repository: **601 dependencies were pinned with sha1**. All three lockfiles
  are regenerated from the public registries and now carry **only `sha512` and
  public URLs**. A new **`Relock`** workflow is the supported way to change a
  dependency (`gh workflow run relock.yml --ref <branch>`); it resolves on a
  clean runner, verifies the result installs *and* builds, then pushes back to
  the branch or opens a PR. A stdlib-only guard (`make lockcheck`, an opt-in
  pre-commit hook via `make hooks`, and a CI job) rejects proxy URLs and weak
  hashes, and `uv sync --locked` in CI turns a stale lock into a failure rather
  than a silent re-resolution. `make sync` now uses `npm ci`, and the Makefile
  exports `UV_FROZEN=1` — every `uv run` re-locks otherwise, so `make dev`,
  `make check`, and `make test` were each rewriting `uv.lock`.

- **MCP: one sign-in prompt instead of one per credential**: the WorkIQ preview
  and the Agent 365 servers are different Entra clients against different
  resources, so they hold separate tokens on separate expiry clocks — which used
  to mean a second banner and a second click every time both aged out. Pending
  sign-ins are now tracked **per credential** and collapsed into a **single
  banner** naming every server involved, with re-auth attempts serialized so two
  flows can't race for the same window. The one **Sign in** you click now also
  **chains the others**: as soon as it succeeds, the remaining credentials retry
  on the hands-free silent path while the Entra SSO session is hot (and the
  backend re-arms the prompt for servers parked on a different stale
  credential), so in the common case they renew with **zero extra clicks**. Set
  `workiq_chain_reauth_enabled=false` to renew each credential only when it's
  independently needed. Previously the second credential never even attempted the
  silent pass and always fell through to a manual click. See
  [MCP → One prompt, not one per credential](https://lrivallain.github.io/precursor/features/mcp.html#one-prompt-not-one-per-credential).

- **MCP: a busy loopback port no longer blocks a WorkIQ sign-in**: each
  OAuth-protected server still *prefers* its fixed callback port (`12798`,
  `12799`, `12800`), but when that port is taken — another Precursor window
  mid-sign-in, or an unrelated app — the flow now falls back to a free
  **ephemeral port** instead of failing fast. Entra ignores the port of a
  loopback redirect for public clients, so the fallback is transparent and
  several windows can sign in concurrently. Set
  `workiq_loopback_port_fallback=false` to restore the previous strict
  behaviour.

- **MCP: sign-in prompts are collapsed per credential on the backend too**: when
  a turn pauses because MCP servers need authenticating, the blocked list is now
  reduced to **one name per credential** before anything is announced, so two
  blocked Agent 365 servers ask you to sign in **once** rather than twice. This
  lands at a single choke point (`auth_blocked_servers`), so chat, topic,
  workspace and scheduled-command pauses all inherit it. Which servers share a
  credential is no longer hardcoded per call site: a new **OAuth server
  registry** is the one place that knows which built-ins sign in, what to call
  them, and which of them are backed by the same token.

- **MCP: keep-alive backs off for credentials you aren't using**: the ticker
  still refreshes a token shortly before it expires so an active session never
  breaks mid-turn, but a WorkIQ server enabled long ago and never called is now
  left alone — no refresh, and crucially **no sign-in prompt** when its refresh
  token finally lapses. Usage is tracked per credential (calling either Agent 365
  server keeps the shared token warm) with a **6 hour** default window, and the
  clock is seeded at process start so a restart doesn't leave everything cold.
  Set `workiq_keepalive_idle_after_seconds=0` to keep every signed-in credential
  warm indefinitely, as before. See
  [MCP → Quiet when you're not using it](https://lrivallain.github.io/precursor/features/mcp.html#quiet-when-you-re-not-using-it).

### Added

- **A Markdown formatting toolbar and keyboard shortcuts on the Markdown
  composers.** The editors that render their value as Markdown — live-session
  **notes** and **summary**, and the GitHub **issue / comment drafts**
  (`/gh-update`, `/gh-create`, and the comment box) — now carry a small
  formatting toolbar (bold, italic, strikethrough, inline code, link, heading,
  quote, and bulleted / numbered lists) that rewrites the current selection in
  place. The same actions are bound to shortcuts — <kbd>⌘/Ctrl</kbd> + <kbd>B</kbd>
  (bold), <kbd>I</kbd> (italic), <kbd>K</kbd> (link), <kbd>E</kbd> (code), and
  <kbd>⌘/Ctrl</kbd> + <kbd>⇧</kbd> + <kbd>X</kbd> (strikethrough). Toggles are
  reversible (re-apply to unwrap), an empty selection parks the caret between the
  markers, and <kbd>⌘/Ctrl</kbd> + <kbd>K</kbd> stays local to the field instead
  of bubbling to the global command palette. The plain-text fields (system
  prompts, memories, roles) are untouched.


  one collection, unread activity in the others was invisible. The switcher now
  carries a dot when another collection has unread messages, and each row in its
  dropdown shows that collection's own unread count.

- **The GPT-5.5/5.6, Codex, Grok and MAI models now actually work.** Copilot
  serves its catalogue across two API surfaces, and a model offered by one is
  rejected by the other — Precursor only ever spoke `/chat/completions`, so
  **eight of the models it listed** (`gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.5`,
  `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `grok-4.5` and
  `mai-code-1-flash-picker`) answered a raw `400` the moment you sent a message.
  Precursor now speaks the **Responses API** as well and routes each model to the
  surface that serves it, reading the `supported_endpoints` the catalogue already
  publishes. Streaming, tool calls, images and reasoning effort behave the same on
  both. Models the catalogue says we can drive with *neither* endpoint are no
  longer offered at all, since listing a model that cannot be called only
  guarantees a failure once it is picked.

- **Type-to-filter in the model pickers**: the composer and agent model menus
  now open with a search box focused, narrowing the list as you type. Matching a
  vendor heading (`anthropic`, `microsoft`, …) keeps that whole group, so the
  catalogue can be sliced by publisher as well as by model name, and <kbd>Enter</kbd>
  picks the first match. Menus with only a handful of options — reasoning effort,
  context size — stay plain lists.

- **MCP: console tracing for the WorkIQ sign-in legs.** The hands-free re-auth
  hides two sequential attempts inside one request — the invisible `prompt=none`
  iframe, then the backend self-opening the OS browser — so when the manual
  banner finally appears there was no way to tell which leg gave up, or why.
  Each step of an auth episode now logs to the browser console under
  `[workiq-auth]` with elapsed timings: when a notice opens, when the
  authorization URL arrives over SSE (published for the silent leg only, so its
  absence is itself diagnostic), how the hidden frame navigated — including
  whether it was refused before any document loaded, the signature of
  `X-Frame-Options` blocking the silent pass outright — and which outcome
  produced the banner. `window.precursorWorkiqAuthTrace()` returns the whole
  episode for pasting into a bug report; `login_hint`, `state` and `nonce` are
  never logged. Silence it with
  `localStorage['precursor.debug.workiqAuth'] = '0'`.

- **Collections**: split topics into separate workspaces of work. A switcher at
  the top of the Topics panel filters the sidebar tree (and the Pinned section)
  to one collection at a time; search, the command palette, and the archive stay
  unfiltered, and opening a topic from another collection switches to it. Move
  topics via the sidebar context menu, topic settings, or the new
  **`/collection <name>`** command — sub-topics always follow their parent.
  Each collection carries a name, description, colour accent, and an optional
  **GitHub repo** override, giving repo resolution a three-step chain:
  `topic.github_repo` → `collection.github_repo` → the global setting. Manage
  them in **Settings → Collections**; deleting one re-homes its topics to a
  destination you choose rather than deleting anything. Existing topics are
  backfilled into a protected **General** collection. The MCP `list_topics` /
  `get_topic` tools now report a topic's collection, and `list_topics` accepts a
  `collection` filter. See [collections](https://lrivallain.github.io/precursor/features/collections).

- **Search with `/`**: pressing <kbd>/</kbd> anywhere outside a text field now
  opens the command palette straight into search — the same launcher as
  <kbd>⌘K</kbd> / <kbd>Ctrl-K</kbd>, one key away. The shortcut stands down
  whenever you're typing (inputs, textareas, and rich contenteditable editors),
  so the composer's `/` slash-command picker is untouched, and it won't stack the
  palette on top of an open dialog.

- **Sidebar contextual menus**: right-click topic, chat, Live, and agent rows for
  the actions each surface supports. Topics and chats expose rename,
  read/unread, pin/unpin, reminder, notes, and archive; agents expose rename,
  read/unread, and archive; Live sessions expose rename and archive.

- **MCP: Microsoft Agent 365 servers (`workiq-teams`, `workiq-user`)**: two new
  built-in, OAuth-protected streamable-HTTP servers covering **Teams** (chats,
  channels, messages, members, presence) and the **directory** (profiles,
  managers, direct reports). They reuse the existing WorkIQ browser sign-in
  stack — silent-first re-auth, keep-alive refresh, inline auth banner — and
  **share a single sign-in**: they authenticate as the same Entra client against
  the same resource, so one token serves both (the WorkIQ preview keeps its own,
  separate one). Their endpoint URL
  embeds a Microsoft **tenant GUID**, resolved from **Settings → MCP →
  Microsoft 365 tenant**, then `PRECURSOR_WORKIQ_TENANT_ID`, then — as a
  convenience — the `tid` claim of a token from an existing WorkIQ sign-in, so
  in practice there is usually nothing to configure. See
  [MCP → Agent 365](https://lrivallain.github.io/precursor/features/mcp.html#agent-365-workiq-teams-and-workiq-user).

- **Installable app (PWA)**: Precursor now ships a web app manifest, PNG icons
  (incl. a maskable variant), and a minimal service worker, so Chromium browsers
  offer to **install it as a standalone app** (own window, dock/taskbar icon).
  The service worker registers only in the built SPA (a one-process
  `precursor` run), does **no offline caching**, and passes all traffic through
  to the network — it exists purely to meet the browser's installability bar.
  It stays a convenience wrapper around your local instance: works while the
  process is running, on the same machine over `localhost`. See
  [Installation → Install as a browser app](https://lrivallain.github.io/precursor/guide/installation.html).

- **MCP: cross-entity search + chats/agents/live accessors**: the built-in
  `precursor` MCP server's `search` tool now spans the same surfaces as the ⌘K
  palette — topics, chats, agents, and live (meeting) sessions — and each hit
  carries an `accessor` hint pointing to the tool that returns its full content.
  Three new opt-in `mcp_expose` sections back this: **`chats`** (`list_chats`,
  `get_chat`, `list_chat_messages`), **`agents`** (`list_agents`, `get_agent`),
  and **`live`** (`list_live_sessions`, `get_live_session` — notes, summary,
  transcript, and insights). Chat/agent/live search hits only surface when their
  section is exposed, so snippets never leak content the host hasn't opted into.

- **Playwright MCP server (authenticated scraping)**: a new built-in `playwright`
  tool server wraps Microsoft's official `@playwright/mcp` (launched via `npx`,
  like `workiq`) so agents, topics, and chats can drive a real browser —
  navigate, read the rendered DOM/text, and screenshot. It runs **headed** and
  defaults to **Microsoft Edge** (`--browser msedge`) so it can ride the
  corporate Edge SSO/WAM broker for authenticated Entra scraping, reusing
  `@playwright/mcp`'s **shared, machine-wide persistent profile** so a sign-in
  already onboarded there (including via other Playwright-MCP tools) carries over
  and reaches **authenticated** pages that `fetch` (raw HTTP, no browser/session)
  can't. `PRECURSOR_PLAYWRIGHT_BROWSER` (default `msedge`) picks the channel and
  `PRECURSOR_PLAYWRIGHT_PROFILE_DIR` pins an isolated profile. Toggle it in
  **Settings → MCP**; a host-dependency preflight requires Node.js (`npx`) on
  PATH.

- **Attach text & code files**: the message composer now accepts plain-text and
  source files (`.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.toml`, `.xml`, `.py`,
  `.ts`, `.js`, `.go`, `.rs`, `.sql`, `.sh`, and many more) in addition to images
  and PDF/DOCX/PPTX. Their UTF-8 text is extracted and folded into the turn as
  context. Because browsers report inconsistent MIME types for source files,
  acceptance falls back to the file extension and the stored type is normalized
  to a `text/*` MIME.

- **Edit transcript phrases inline**: in a Live session, **double-click any
  phrase** in the transcript to correct misheard words in place — press
  <kbd>Enter</kbd> (or blur) to save, <kbd>Esc</kbd> to cancel. The correction
  persists to the session and feeds the insights and summary. Backed by a new
  `PATCH /api/live/{id}/segments/{segment_id}` endpoint.

- **Live transcript auto-cleanup**: a Live session's raw transcript is now
  automatically deleted a configurable number of days after the session ends
  (**7 by default**; `0` keeps it forever), bounding database growth. Only the
  transcript is removed — the session and its insights, notes and summary are
  preserved — and only *ended* sessions are eligible, so an active or paused
  recording is never touched. Configure it under **Settings → Live**
  (`live_transcript_retention_days`); the sweep runs on startup and daily via a
  background ticker.

- **Past meetings in the Live agenda picker**: the meeting lists used to *start*
  a Live session (Start screen) and to *attach* one (Context tab) now include the
  **last 7 days** of calendar meetings, not just today, so you can record or
  summarize from a meeting that already happened. Entries are split by a
  color-coded **Past** (amber) vs **Current & upcoming** (emerald) marker; the
  list auto-scrolls to that boundary so today's meetings are front-and-centre,
  and the past group is capped to the **10 most recent** meetings. Past meetings
  still spin up a session linked to the meeting (handy with **From Teams
  transcript**).

- **Summarize a Live session from the Teams transcript ("no local record")**:
  when a Teams meeting is linked to a Live session and the **WorkIQ MCP** server
  is enabled, the **Summary** tab gains a **Generate from Teams transcript**
  button. It scrapes the meeting's published transcript through WorkIQ (Microsoft
  Graph:
  `/me/onlineMeetings` → `/transcripts` → VTT `/content`) and generates
  Precursor's own structured recap — so you don't need to capture the meeting
  audio locally. Best-effort and fail-closed: it requires you to be the meeting
  organizer, the delegated `OnlineMeetingTranscript.Read.All` permission, and a
  transcript that Teams has already published (a few minutes after the meeting
  ends). The **Summary** tab is always available; its actions light up based on
  the data at hand — generate from the local recording and/or from the Teams
  transcript. New endpoint `POST /api/live/{id}/summary/from-transcript`; the
  linked meeting now also carries its Teams join URL.

- **Hands-free WorkIQ re-auth**: when a WorkIQ preview session's refresh token
  ages out, Precursor now attempts the silent `prompt=none` authorization in an
  invisible iframe before showing anything. If the browser still holds a live
  Entra SSO session the session is renewed with **zero clicks** and the
  `McpAuthBanner` never appears; the manual **Sign in** banner only surfaces when
  a silent pass genuinely needs interaction (or iframe framing / third-party
  cookies block it). Gated by the new `workiq_auto_reauth_enabled` setting
  (default on); turn it off to always require the manual click.

- **In-app documentation**: the documentation site (the VitePress project in
  `website/`) is now served by the app itself at `/docs/`, reachable from a
  **Documentation** entry in the command palette and the About dialog (the About
  link opens the local `/docs/` in dev and the public site in a production
  build). In production it's pre-built with base `/docs/` (`make docs`), bundled
  into the wheel, and served statically with VitePress clean-URL resolution; in
  `precursor --dev` a live VitePress dev server runs on a hidden port that the
  SPA's Vite proxies `/docs` to, so editing any `website/**` markdown
  hot-reloads in the browser. GitHub Pages hosting is unchanged — it builds the
  same source with the default base `/` in its own workflow (`DOCS_BASE`
  selects the base). FastAPI's interactive API docs moved to `/api/docs`,
  `/api/redoc`, and `/api/openapi.json` so the root `/docs` path is free for the
  product docs.

- **Reorderable sidebar sections**: drag any section (Topics, Chats, Live,
  Agents, Files, Kanban) in the sidebar to rearrange it. Works in both the
  vertical icon rail and the horizontal tab switcher, with an insertion line
  showing exactly where the section will land (drop on either side of a
  neighbour, including the ends). The order is persisted in the browser
  (`precursor:sidebar:sectionOrder`) and shared between the two navigation
  styles; newly-shipped sections append to the end without disturbing an
  existing arrangement.

### Changed

- **Hands-free WorkIQ sign-ins close their window instantly**: the loopback
  success page now closes immediately for the silent and self-triggered
  (`auto`) re-auth passes instead of showing the manual flow's two-second
  "Closing this tab in…" countdown. Nobody is watching an automatic sign-in, so
  it no longer leaves a stray window sitting around after it succeeds; the
  manual, banner-driven sign-in keeps its brief confirmation beat.

- **WorkIQ re-auth is now hands-free and self-triggering**: when the WorkIQ
  refresh token ages out, Precursor prefers automation over interrupting you. It
  runs the silent `prompt=none` pass in an invisible iframe first and, when that
  needs interaction, **self-opens your OS browser** to a single visible sign-in —
  no `McpAuthBanner` click and no redundant second prompt. The manual banner only
  surfaces as a last resort (auto re-auth off, loopback port busy, or declined).
  A new `auto=true` mode on `POST /api/mcp/servers/workiq/reauthenticate` drives
  this; the interactive fallback opens the OS browser instead of the iframe so it
  never races the loopback port.

- **Cleaner API error messages**: failed API calls now surface just the server's
  human-readable `detail` text (e.g. *"The linked meeting has no Teams join
  link…"*) instead of the raw `400 Bad Request: {"detail":"…"}` envelope. The
  HTTP status is only shown as a fallback when the response carries no detail.

- **Live record controls moved into the Transcript tab**: the **Record** button,
  capture-device picker, **+ mic** mix-in and meeting **language** now live pinned
  at the top of the **Transcript** tab (instead of the session toolbar), so they
  stay visible even when the transcript grows to fill the height. Session-level
  controls — topic, features, **End session**, archive and delete — remain in the
  toolbar above the tabs.

- **Sidebar auto-scrolls to the active item**: opening a topic, chat, agent,
  live session, workspace, or Kanban project from a deep link, the ⌘K command
  palette, or a search now scrolls its sidebar list so the selected row is
  brought into view instead of being left off-screen. Rows already visible stay
  put.

- **Live recording is clearer to start and harder to lose**: the **Record**
  button now shows a transient **Starting…** state while it connects to Azure
  (token, SDK, capture device) instead of looking unresponsive for the couple of
  seconds before it turns red. While a recording is live, leaving the screen —
  switching cockpit, going Home, opening another live session, or jumping via
  search — now asks you to confirm (**Keep recording** / **Leave & stop
  recording**), and reloading or closing the tab triggers the browser's native
  leave prompt, so an accidental navigation no longer silently drops the capture.

- **Vertical navigation rail on the home launcher**: when the sidebar uses the
  vertical icon rail (not the horizontal tabs), that rail now also shows on the
  home launcher, so switching sections is always one click away. Home and the
  ⌘K search launcher are grouped together at the top of the rail — Home, then
  Search, then a separator, then the sections — consistent across the home rail,
  the expanded rail, and the collapsed sidebar.

### Fixed

- **Orphaned `running` agents now recover on boot, and a degraded runtime is
  visible.** A dev auto-reload (or any process death) mid-turn used to leave an
  agent pinned in `running` with no live worker behind it, because the boot-time
  reap and the watchdog only ran *after* the SDK client successfully started —
  so if the client failed to come up, the row stayed stuck forever and never
  timed out. The reap (`running` → `interrupted`, resumable) now runs early on
  **every** boot, before and regardless of the SDK client, so orphans are always
  un-stuck. Settings also gains an `agents_runtime_started` signal (distinct from
  the stateless `agents_available` probe) with a warning banner when the runtime
  is installed but didn't actually start in this process — telling you to restart
  to recover instead of silently doing nothing.
- **Agents no longer brick when the default model goes stale.** The Copilot
  runtime's model catalogue rotates over time, so a model id that was valid when
  it was saved (e.g. a since-retired `claude-sonnet-4.5`) can vanish. Passing a
  now-unknown id to the SDK fails the whole turn at session build, and because a
  turn is dispatched as a detached background task the exception was **swallowed**
  — the agent hung forever on its "Sending…" spinner with no error. Three fixes:
  the factory default is now **`auto`** (the runtime picks a current model, so it
  can never go stale); a per-turn guard **downgrades a vanished model pin to
  `auto`** before the session is built (leaving `auto` and still-valid pins
  untouched, and never masking a transient runtime outage); and any turn-dispatch
  error is now surfaced as a **`failed`** status with the error text instead of a
  silent hang.

- **A stale WorkIQ / Agent 365 sign-in no longer strands you behind a 409.**
  When a manual sign-in was orphaned — its popup closed, tab reloaded, or the OS
  browser walked away (the standalone-PWA/OS-browser flow has no popup to watch
  in the first place) — the backend kept the shared *auth-family* lock parked on
  the loopback callback for up to 180s, so every retry was refused with **"A
  WorkIQ … sign-in is already in progress"** with no visible flow to finish or
  cancel. Now an explicit interactive retry **preempts** the stale flow (it
  signals it to abort, frees the loopback port, and takes over once the lock
  releases within ~5s); a genuinely near-complete sign-in whose redirect already
  landed is still left to finish, and silent/auto background passes never disturb
  a live flow. The SPA also fires a best-effort `sendBeacon` cancel on page
  unload (`pagehide`), so a reload or close releases the family lock immediately
  instead of orphaning it.
  splitting topics into collections narrowed the app's single topic tree to the
  active collection, and that tree is what the rest of the UI reads to *resolve*
  a topic — not just what the sidebar renders. So the **Live session topic
  picker listed only the current collection's topics** (both when linking an
  existing session and when starting one), the Live header's *open topic* link
  and the agent header's topic chip disappeared or fell back to a literal
  "Topic" whenever the linked topic lived elsewhere, stream-completion
  notifications lost their title, and the Topics unread badge and tab title
  undercounted. The tree is global again and the collection filter is applied
  where it belongs — the sidebar and the *parent topic* pickers — so switching
  collections is now instant client-side filtering rather than a refetch. Since
  the app-wide topic pickers (Live session linking and agent topic association)
  now span every collection, each root topic in them carries its collection's
  accent dot and name so same-named branches are easy to tell apart.

- **The composer model picker served a frozen catalog**: the shared model store
  fetched `/api/llm/models` exactly once per app lifetime and nothing ever
  invalidated it, so a list captured on first launch stayed pinned forever.
  Because the desktop webview never reloads, that snapshot could be weeks stale
  — showing models the provider has since retired while hiding newly added ones
  (MAI-Code-1-Flash was missing for exactly this reason). Since the model
  dropdown moved out of Settings into the composer, that store is the *only*
  model list in the UI, so there was no way to refresh it short of restarting
  Precursor. The catalog is now refetched when it is older than five minutes and
  the picker is opened, Settings seeds the shared store on load and on
  **Apply & refresh models** (previously the button only refreshed the settings
  panel's private copy, despite its label), and saving a new GitHub token forces
  a refresh because a different account can mean a different catalog.

- **A long-running instance served a stale Copilot model catalogue**: the
  `gh auth token` result was cached for the whole process lifetime, and GitHub
  scopes entitlements — including which models the picker may offer — to the
  token itself. An instance left running therefore kept serving the catalogue as
  it looked when it first resolved a token, offering models that had since been
  retired while hiding newly added ones; `MAI-Code-1-Flash` was missing for
  exactly this reason, and the only cure was restarting Precursor. The CLI token
  is now re-read when it is older than five minutes, and saving a GitHub token in
  Settings drops the cached one immediately.

- **WorkIQ sign-in in the installed PWA**: clicking **Sign in** (the banner or
  Settings) from Precursor running as an installed standalone PWA did nothing —
  standalone display mode heavily restricts `window.open`, so the script-opened
  sign-in popup couldn't be created, steered to the authorization URL, or
  auto-closed. The manual sign-in now detects standalone PWA mode and drives the
  OAuth flow through the **OS default browser** instead (the same surface the
  hands-free re-auth already uses), clearing the banner over SSE on success.

- **Noisy WorkIQ sign-in timeouts**: an interactive WorkIQ sign-in the user never
  completed (walked away or closed the tab without the popup's proactive cancel
  firing) used to dump a misleading `ERROR ... OAuth flow error` traceback from
  the MCP SDK and then fail the endpoint with an opaque `502 Bad Gateway`. That
  timeout is now a dedicated, benign `WorkIQAuthTimeoutError`: its SDK traceback
  is suppressed (like the silent-pass and background `needs_auth` signals) and the
  reauthenticate endpoint re-surfaces the manual sign-in banner (a normal
  `interaction_required` response) instead of a gateway error.


  briefly showed the opening user message twice. The backend persists the user
  turn immediately, so a panel mounting mid-stream could race a first-page fetch
  that already included it while the same turn also lived in the live buffer.
  Merging the persisted window with the streaming buffer now dedupes by message
  id, so the first turn renders once.

- **Stale WorkIQ sign-in banner across other windows**: when a WorkIQ sign-in
  was renewed in one window (its popup, the OS-browser tab the hands-free flow
  self-opens, or a silent pass), every *other* open window kept showing the
  "WorkIQ needs you to sign in" banner — they never made the request, so nothing
  told them the credentials were fresh. The reauthenticate flow now broadcasts a
  cross-window `mcp.auth_resolved` event on success, so those windows drop the
  banner (and any "Signing in…" state) immediately, without a reload.

- **Spurious WorkIQ sign-in prompt when preview mode is off**: an agent turn
  whose `workiq` tool call errored would surface a "WorkIQ needs you to sign in"
  banner even with preview mode disabled — where WorkIQ runs as local stdio with
  no OAuth, so the sign-in couldn't proceed (clicking it returned "Enable WorkIQ
  preview mode before signing in"). The failed-tool auth check now short-circuits
  unless preview mode is on, so routine stdio tool errors no longer nag for an
  impossible sign-in.

- **Live session opened on the wrong tab**: because the split-panel layout
  (including the active tab) is persisted globally, a freshly created Live
  session would open on whatever tab was last active — typically **Summary**
  from a previous session — instead of the transcript. New sessions now land on
  the first tab so you start at the top of the panel.

- **Microphone indicator stuck on after a Live recording**: stopping a recording
  released the captured audio tracks only from inside the Speech SDK's
  stop/close callbacks, which may never fire when the socket is already gone —
  leaving the browser/OS capture indicator lit until a page reload. Teardown now
  stops the tracks synchronously, so the indicator clears the instant you stop.

- **Double-started Live recording duplicating the transcript**: starting a Live
  recording twice at nearly the same instant (e.g. a quick double-click on
  **Record**, or a second view racing to start during a transition) could spin
  up two capture sessions against the same meeting audio — every phrase was
  transcribed twice, and stopping one left the other orphaned and still
  streaming. A single **app-wide recording lock** now guarantees only one live
  transcription runs at a time: a second start is refused (with a clear message)
  until the active one is stopped. This complements the existing per-view
  double-click latch by also covering distinct transcriber instances.

- **WorkIQ sign-in stuck on "Signing in…" when another Precursor window is
  open**: the OAuth callback uses a *fixed* loopback port
  (`127.0.0.1:12798`, matching the registered `redirect_uri`), so only one
  process per machine can run it. With several Precursor instances open (e.g.
  multiple worktrees), a sign-in launched while another instance already owned
  the port would clear the stored tokens and then hang on "Signing in…" until
  the 300s callback timeout — its browser redirect delivered to whichever
  instance held the port — leaving the server unauthenticated. The interactive
  sign-in now **preflights the loopback port** and fails fast with a clear,
  actionable **409** ("The WorkIQ sign-in port 12798 is already in use — another
  Precursor window or app is signing in…") *before* touching the existing
  tokens, so a still-usable session is preserved and the banner shows what to do
  instead of stranding. The hands-free silent pass simply defers to the manual
  banner when the port is busy. To further limit contention, **closing the
  sign-in popup now cancels the flow** (the SPA calls a new
  `POST /api/mcp/servers/workiq/reauthenticate/cancel`), so an abandoned sign-in
  releases the port at once instead of squatting it, and the interactive
  callback timeout is trimmed from 300s to 180s as a backstop.


  iframe; when framing or third-party cookies block it (or there's no live SSO
  session) the loopback never fires and the pass times out. That timeout was a
  plain `RuntimeError`, so the MCP SDK logged a full `ERROR OAuth flow error`
  stack trace on startup even though the pass is a *handled* fallback to the
  manual "Sign in" banner. A silent-pass timeout now raises the same
  `WorkIQInteractionRequiredError` as Entra's `interaction_required` — kept out
  of the logs by the existing suppression filter — while a genuine *interactive*
  (user-driven) sign-in that times out still surfaces loudly.

- **WorkIQ sign-in aborted by stray loopback probes**: the OAuth callback
  server resolved on the *first* inbound connection regardless of content, so a
  favicon fetch, browser/OS connectivity probe, or pre-connect that carried no
  `code`/`error` failed the whole flow with `RuntimeError: No authorization code
  in OAuth callback` (often right after a silent `prompt=none` pass timed out).
  The loopback now answers such non-OAuth requests with `204 No Content` and
  keeps listening for the genuine redirect (or the outer timeout).

- **WorkIQ OAuth callback never returned the auth code**: the loopback
  callback's `asyncio.start_server` block was mis-indented inside the
  per-connection handler, so `_callback_handler` fell off the end and returned
  `None`. The SDK's `auth_code, state = await callback_handler()` unpack then
  crashed with `TypeError: cannot unpack non-iterable NoneType object`,
  breaking every interactive and silent WorkIQ sign-in. The server now binds
  and awaits the redirect in the handler body as intended.

- **WorkIQ sign-in 502 hid the real error behind anyio's task-group wrapper**:
  a failed interactive re-auth reached the SPA as
  `WorkIQ sign-in failed: unhandled errors in a TaskGroup (1 sub-exception)` —
  the MCP SDK's streamable-http transport raises inside a task group, so the
  actual cause (a sign-in timeout, a transport blip, a missing authorization
  code) was buried in a `BaseExceptionGroup`. The `/api/mcp/servers/workiq/reauthenticate`
  endpoint now unwraps the group (and the `__cause__`/`__context__` chain) to its
  leaf exception(s), so the 502 detail names the real reason.

- **In-app docs (`/docs/`) were silently unavailable in `precursor --dev`**:
  the live VitePress docs server only starts when `website/node_modules` is
  present, and a fresh checkout that ran `precursor --dev` without `make sync`
  first would skip it with just a log warning. The **Documentation** link then
  fell through to the SPA (or the backend's "Docs are not built" message on the
  API port). `--dev` now auto-installs the docs dependencies on first run
  (mirroring the frontend auto-build), so `/docs/` works out of the box; it
  still degrades to disabling live docs (never failing the stack) when npm is
  unavailable.

- **About dialog had two links to the same site**: the **Documentation** and
  **Website** rows in the About modal both pointed at
  `precursor.vuptime.io`. The redundant **Website** row is gone — a single
  **Documentation** link (local `/docs/` in dev, the public site in a
  production build) now covers it.

- **In-app version showed a stale dev build after the `precursor-ai` rename**:
  version resolution still queried the old `precursor` distribution name, which
  raised `PackageNotFoundError` and silently fell back to the build-time
  `_version.py` — so the **About** modal could report a stale version (e.g.
  `0.0.1.dev…`) instead of the installed/tagged one. It now resolves the
  `precursor-ai` distribution.

- **Deprecated `uuid@9` warning on frontend install**: the Azure Speech SDK
  (`microsoft-cognitiveservices-speech-sdk`) pins `uuid@^9.0.0`, which npm flags
  as no-longer-supported. A frontend `overrides` entry now forces `uuid@^14`
  (the version already resolved for the rest of the tree via mermaid), so the
  install is deprecation-free and dedupes to a single `uuid`. The SDK only uses
  `uuid.v4()`, which is unchanged across these majors.

### Fixed

- **MCP: Agent 365 servers never attached to agent sessions**: `workiq-teams`
  and `workiq-user` were rejected by name when an agent turn resolved its bearer
  token, so despite being signed in they were dropped from the agent's tool
  catalog, their tools silently went missing, and a sign-in prompt was raised
  that **signing in could never clear** — the same name gate blocked the retry,
  so the prompt came back on every rebuild. Bearer resolution and tool-failure
  attribution now go through the OAuth server registry, which covers every
  OAuth-protected built-in rather than the WorkIQ preview alone.

## [2026.7.0] - 2026-07-19


### Added

- **PyPI publishing on release**: the **Release** workflow now publishes the
  wheel + sdist to [PyPI](https://pypi.org/project/precursor-ai/) on every `v*`
  tag, in addition to the GitHub Release. Publishing uses **Trusted Publishing**
  (OIDC) via a dedicated `pypi` environment — no API token to store or rotate. The
  workflow is split into `build` → (`github-release`, `pypi-publish`) jobs that
  share a single built artifact. See [RELEASING.md](RELEASING.md) for the one-time
  PyPI/GitHub environment setup.

- **PyPI distribution name is `precursor-ai`**: the plain `precursor` name was
  already taken on PyPI, so the published distribution is **`precursor-ai`**. It
  ships a matching **`precursor-ai`** command (so `uvx precursor-ai` needs no
  `--from`) plus a shorter **`precursor`** alias; the import package is unchanged.
  Install with `uv tool install precursor-ai` / `pip install precursor-ai`, or run
  it ad-hoc with `uvx precursor-ai`.

- **Website link in the About dialog**: the **About Precursor** dialog (persona
  menu) now links out to the project website at
  [precursor.vuptime.io](https://precursor.vuptime.io/), alongside the source-code
  and report-an-issue links.

- **Tool-result retention**: a new **Settings → System → Storage / retention**
  option (`tool_result_retention_days`, default `0` = keep forever) bounds
  long-term DB growth from large persisted tool outputs. Past the configured age,
  a `tool` message's `content` is replaced **in place** with a short placeholder;
  the row and its `tool_calls` metadata are preserved so conversation history
  still pairs each assistant tool-call turn with its results (no turns are
  dropped). The sweep runs best-effort on startup and periodically via a
  lightweight ticker (gated by `scheduler_enabled`); it only touches `tool` rows
  older than the cutoff whose content exceeds a small floor and isn't already the
  placeholder, so re-runs are cheap and idempotent.

- **Agent unread badges & notifications**: agent sessions now track unread
  activity just like topics and chats. When a background or scheduled agent
  produces a new reply while you aren't looking at it, its row in the Agents list
  is bold with an unread count, and — when notifications are enabled and the
  window is unfocused — a browser notification fires. Opening a session clears
  its badge (`POST /api/agents/{id}/read`); the count (assistant replies since
  `last_read_at`, exposed as `AgentSessionRead.unread_count`) is computed
  server-side from the archived event timeline. The sidebar mode switcher
  (Topics / Chats / Agents) now highlights any tab with unread items — a count
  badge when expanded, a dot on the collapsed icons and the overflow menu — and
  the browser tab title reflects the combined unread total.

- **WorkIQ preview keep-alive**: a background ticker now silently refreshes the
  WorkIQ preview OAuth token before it expires, so the hosted session survives
  without frequent interactive re-sign-in. It only acts while preview is enabled
  and a token already exists (it never starts a sign-in on its own), refreshing
  once the access token is within a margin of expiring. When the refresh token
  itself has aged out and a silent refresh can no longer proceed, it surfaces the
  existing `McpAuthBanner` re-authenticate prompt once (a tenant Conditional
  Access sign-in-frequency policy still forces periodic interactive sign-in).
  Tunable via `workiq_keepalive_enabled`, `workiq_keepalive_poll_seconds`, and
  `workiq_keepalive_refresh_margin_seconds`.

- **Lazy-loaded conversation history**: discussions no longer load their entire
  transcript up front. Topics and chats fetch the most recent page (50 messages)
  and pull older ones in as you scroll toward the top, preserving your scroll
  position. The message list endpoints (`GET /api/topics/{id}/messages` and
  `GET /api/chats/{id}/messages`) gained optional `limit` + `before_id` cursor
  params (no params still returns the full transcript). Agent timelines apply the
  same idea client-side, windowing the rendered workflow steps so very long runs
  don't mount thousands of nodes at once. Shared scroll behaviour lives in the new
  `useChatScroll` hook.

- **Skills are shared `SKILL.md` files**: skill content (name, description,
  instructions) now lives in `<copilot_home>/skills/<name>/SKILL.md` files using
  the GitHub Copilot CLI's format (YAML frontmatter + markdown body), so skills
  are interoperable with the CLI and other tools. The skills folder is detected
  the way the CLI resolves its home (`COPILOT_HOME` → `XDG_CONFIG_HOME/copilot`
  → `~/.copilot`), with a `PRECURSOR_SKILLS_DIR` override. Skills authored by
  other tools are **discovered** in the Skills tab and can be enabled per skill
  (disabled by default); enable/disable, edit, export, and delete all operate on
  the file. The `skills` table is reduced to an enablement record — if a file is
  renamed or deleted, its enablement is dropped. Pre-existing Precursor skills
  keep working as **legacy** entries and gain a **Migrate** button that writes
  the `SKILL.md` and keeps the row as an enablement record. New
  `services/skills.py` plus a name-keyed `/api/skills` (now with
  `/{name}/migrate`).
- **Memory management commands**: long-term memories can now be created, listed,
  and edited without leaving a conversation. New `/memory-store [kind] <content>`,
  `/memory-list`, and `/memory-update <id> [kind] <content>` slash commands work
  on the topic and chat surfaces (store/update also on agent sessions and headless
  scheduled topic runs; `/memory-list` surfaces the ids needed by `/memory-update`).
  The built-in `precursor` MCP server gained `store_memory` / `update_memory`
  tools — gated by a new `memory_write` capability toggle, alongside the existing
  `list_memories` read tool — so the model itself can record or refine memories.
  Memories are now also injected into **agent** sessions' system context (they
  already fed topic and chat turns), so standing preferences and facts follow you
  everywhere. Shared parsing/persistence lives in `services/memories.py`.
- **Detach the Notes / GitHub draft panel into its own window**: the shared
  command panel (used by `/notes`, GitHub issue/comment create, and issue update
  drafts) now has a pop-out button in its header that hands the panel off to a
  separate native browser window so it can live outside the current tab while you
  keep editing. The detached window **survives navigating to another topic or
  chat** in the main app — it stays fully functional and bound to its *original*
  conversation, so every action (add to chat, add & ask AI, post comment, save
  draft, attachments, rephrase, GitHub create/update/close) still targets the
  container it was popped out from. The window mirrors the app's stylesheets and
  theme (incl. dark mode) and closes automatically once you take a terminal
  action. Closing a notes window saves the in-progress text as a recoverable
  server-side draft; GitHub draft windows discard on close. Implemented with an
  app-level host (`DetachedDraftHost`) backed by a global store
  (`detachedDraftStore`) plus self-contained controllers, rendered through a
  dedicated React root inside the popup (`DetachedWindowPortal`) so typing and
  button clicks work across the window boundary.
- **Chat description as context or system prompt**: a chat's description now
  feeds the model. By default it's injected once as discussion-level context; a
  new **"Use as system prompt"** checkbox next to the description (in chat
  settings) instead enforces it as an instruction prepended to every user turn.
  Empty descriptions are a no-op and the checkbox is disabled until you type one.
  The flag persists with the chat (`description_as_system_prompt`); a Role and a
  system-prompt description coexist deterministically (role persona in the system
  message, description enforced per turn).
- **Chats**: flat conversation sessions alongside the topic tree, reachable from
  a sidebar mode switcher (**Topics · Chats · Files**) while the persona/settings
  menu stays visible across every mode. Chats are "a topic without the tree or
  GitHub issue" — they support the full conversation toolkit: streaming replies
  with the MCP tool loop, skills and slash commands (`/rename`, `/pin`, `/unpin`,
  `/clear`, `/archive`, `/notes`), a live stats panel, dictation, message
  delete + undo, history recall, pin, archive, and unread badges. A **chat
  settings** drawer adds rename/description plus a **Promote to topic**
  transform that moves the transcript into Topics. The **Archive** view is now
  unified (Topics + Chats tabs) and restores each item into its own mode. New
  endpoints under `/api/chats/*`, `/api/chats/{id}/messages/*` (incl. `/notes`
  and `/promote`); the topic streaming generator and the stream store were both
  refactored to be container-agnostic so chats and topics share one code path.
- Chats now support **image attachments** as well (paperclip, drag-and-drop, or
  paste), matching topics — uploaded images are bound to the turn and sent to
  vision-capable models. Both message composers were unified into one shared
  `Composer` component, so topics and chats stay in lock-step.
- **`/notes` image support (topics + chats)**: Notes now accept pasted/uploaded
  images, persist them in the note draft, and show inline previews in the Notes
  pad. "Add to chat" and "Add & ask AI" both carry those images into the created
  user turn, and "Post as comment" uploads note images to GitHub attachments and
  rewrites the comment markdown to use GitHub-hosted image URLs.
- **Friendlier startup / multi-instance**: one `--port` now controls everything.
  In `--dev` the Vite UI runs on the API port **+ 1** and its `/api` proxy
  follows the backend port, so a single flag spins up a full instance. A busy
  port **auto-bumps** to the next free one (checked across IPv4 + IPv6) so
  parallel instances never collide — pass `--strict-port` to fail instead, or
  `--port 0` for an OS-assigned port. A startup banner prints the URL to open,
  `--open` launches the browser, and `--dev` auto-allows the Vite origin via
  CORS. `.env.example` is now fully optional (every setting has a built-in
  default).
- Browser notifications when an assistant turn finishes (including scheduled
  tasks) while the Precursor window isn't focused — opt-in via Settings → Chat →
  Notifications (asks for browser permission on enable). The number of unread
  messages always shows in the tab title (`(N) Precursor`), regardless of the
  notification setting.
- Multiple **LLM providers**, selectable at runtime in Settings → Model: GitHub
  Copilot, GitHub Models, **Azure AI Foundry**, OpenAI, Mistral, Hugging Face,
  Ollama, and Mock. Providers are declared in a registry
  (`services/llm/registry.py`) — adding one is a single entry plus an
  implementation. `GET /api/llm/providers` exposes each provider's config
  fields so the UI renders the right inputs (secrets redacted on read), and the
  Model panel shows discovered-model metadata (summary, context window, tags)
  with a manual model-id fallback when a provider has no catalog.
- CalVer versioning derived from git tags (hatch-vcs); a single source of truth
  replaces the previously hardcoded version literals.
- `GET /api/version` endpoint and a version line in the Settings panel.
- `version` field on `GET /api/health`.
- CI workflow (lint, format, type-check, tests, frontend build) on PRs and
  pushes to `main`.
- Release workflow: pushing a `v*` tag builds the wheel + sdist and publishes a
  GitHub Release with auto-generated notes.
- `SECURITY.md` (threat model + private vulnerability reporting) and a
  Security & deployment section in the README documenting the single-user,
  local-first, no-auth model.
- Dependabot config for pip, npm, and GitHub Actions.
- Contributor prompt helpers (`.github/prompts/`): `/ship-change` and
  `/release` workflows.
- The build-in command panels (`/notes`, `/gh-update`, `/gh-create`,
  `/gh-close`) now share a single `CommandPanel` rendered as a **floating
  window** — draggable by its header and resizable from a corner grip, with
  position and size remembered per panel. Detaching them from the chat layout
  means the scratch pad / draft cards no longer share vertical space with the
  message composer, so each is sized independently. The panels also gained a
  consistent Edit/Preview (Markdown) toggle — `/notes` previously had none.
- Speech-to-text dictation in the chat composer via **Azure AI Speech**. A mic
  button streams interim results into the draft live and appends each finalized
  phrase. Configure the resource endpoint, key, and language in Settings →
  Speech-to-text (with a "Test connection" button). The key is stored
  server-side and never returned; the browser only receives a short-lived token
  minted by the backend (`GET /api/stt/token`), and talks to Azure directly via
  the Speech SDK (lazy-loaded, so it doesn't bloat the default bundle). The mic
  is hidden when Azure isn't configured.
- Chat slash commands for topic actions: `/clear` (erase the transcript, with
  confirmation), `/archive` (archive the topic and leave it), `/rename
  <title>`, `/new <title>` (create a child topic and switch to it), and
  `/pin` / `/unpin` — quick keyboard alternatives to the existing buttons.

### Changed

- **WorkIQ sign-in now opens in a self-closing popup.** The interactive WorkIQ
  OAuth flow used to open in a browser tab via the backend's `webbrowser.open`;
  that tab could never auto-close (browsers only let a script close a window a
  script opened), so it lingered after sign-in. The SPA now opens the sign-in in
  a script-opened popup (synchronously on click, so popup blockers don't catch
  it) and navigates it to the authorization URL the backend surfaces over the
  `/api/events` bus (`mcp.auth_url`); the loopback callback page then closes the
  popup itself once auth completes. The OS-browser path remains as a fallback for
  when no popup could be opened (`use_popup` unset on the reauth request).


  attachments (images, PDF, DOCX, PPTX) used to be stored as `LargeBinary`
  BLOBs in SQLite, which bloated the database file and made every backup/copy
  pay for the payload. They are now written as content-addressed files under
  `settings.blobs_dir` (`.precursor/blobs/<aa>/<bb>/<sha256>`, sharded like
  Git's object store); the `attachments` / `note_draft_attachments` rows keep
  only metadata plus a `sha256` pointer. Identical uploads dedupe to one file
  automatically, and a best-effort startup sweep removes blobs no row
  references. The migration spills existing BLOBs to disk before dropping the
  `data` column (and the downgrade reads them back). No API shape change — the
  attachment endpoints and schemas are unchanged.


  hand-written backfill. `init_db` runs `alembic upgrade head` on startup, which
  both builds a fresh database and migrates an existing one (additive only —
  existing tables are never rebuilt or dropped). The dev-only column backfill /
  table-rebuild path (`_ensure_dev_columns` and friends) is gone, and the prior
  incremental migrations were squashed into a single `0001_baseline` (verified
  to reproduce the old `create_all` schema exactly). A schema change is now one
  Alembic migration that applies to dev and prod alike, and you can generate it
  from your model edits with `make migration m="…"` (autogenerate) → review →
  commit. A database stamped at a now-squashed revision is re-adopted to the
  baseline automatically on next startup (a version-row update only; no schema
  or data change), so no manual step is needed.
- **Breaking (dev/config):** the LLM provider and GitHub token are no longer
  read from the environment (`PRECURSOR_LLM_PROVIDER`, `GITHUB_TOKEN`). They now
  live in the app settings and are configured in the UI, so they can change at
  runtime without a restart. The GitHub token still falls back to your
  `gh auth login` session. The LLM provider factory is now resolved per request
  from the DB.
- `ruff` now ignores `B008` (FastAPI `Depends()` idiom); the lint gate passes
  clean across the repo.
- `mypy precursor` passes under `strict` and is a hard CI gate.
- **uv** is the documented tool for the Python env, running, building, and
  releasing (README, CONTRIBUTING, Makefile, RELEASING).
- The built wheel is now **self-contained**: a conditional build hook
  (`hatch_build.py`) bundles the SPA inside the package for distribution builds
  (not editable installs), so `uvx precursor` / `uv tool install precursor`
  serve the UI with no extra files.
- `.env.example` LLM section reconciled with `config.py` (lists all three
  providers; default `github_copilot`).
- Docs: rewrote `docs/architecture.md` to current state (scheduler, workspaces,
  command-runner jail, skills/memory, real MCP transports, three LLM providers);
  clarified GitHub token resolution (`GITHUB_TOKEN` → `gh` CLI → mock) and the
  dev-vs-prod port model (Vite `:5173` proxy → backend `:8000`).
- Frontend dependencies upgraded (Vite 8, TypeScript 6, `@vitejs/plugin-react`
  6, `react-markdown` 10, `lucide-react` 1). `lucide-react` 1 removed brand
  icons, so the GitHub mark now ships as a local `GithubIcon` component.
- Migrated the frontend to **Tailwind CSS v4** (CSS-first config): theme tokens
  moved into an `@theme` block in `index.css`, dark mode via `@custom-variant`,
  and the `@tailwindcss/vite` plugin replaces the PostCSS setup (no more
  `postcss.config.js` / `tailwind.config.js`).
- Dependabot now groups only minor/patch bumps; majors get their own PR so a
  breaking upgrade (e.g. Tailwind v4) is never bundled with safe ones.
- Unified backend logging: a single `logging.config.dictConfig` (applied at
  startup and passed to uvicorn as `log_config`) gives every record — app,
  uvicorn, and third-party (httpx, mcp, watchfiles) — one human format with an
  ISO-8601 UTC timestamp, level, and logger name. Modules now use
  `getLogger(__name__)` (no hardcoded `precursor.*` names) and operational
  `print()` calls became logger calls. App `debug` stays app-only: noisy
  libraries (aiosqlite, SQLAlchemy, sse-starlette, …) are pinned to fixed levels
  so turning on app DEBUG doesn't unleash per-statement library spam. Output is
  ANSI-coloured when stderr is a TTY and plain when piped/redirected. The
  in-tree stdio MCP servers (fetch / workspace-fs / cmd-runner / precursor)
  apply the same config in their entrypoints, so their `mcp.server` logs share
  the format instead of FastMCP's timestamp-less default; routine `mcp.client`
  connection chatter (session IDs, protocol negotiation) is quieted to WARNING.
- The topic header's GitHub status icon is now struck through with a red
  diagonal when no issue is linked, so the unlinked state reads at a glance.

### Fixed

- **WorkIQ preview no longer hands agents a dead token (or spams the log).** When
  the WorkIQ refresh token has aged out, the SDK's streamable-http transport
  raises our non-interactive `WorkIQAuthRequiredError` wrapped in a
  `BaseExceptionGroup` ("unhandled errors in a TaskGroup"). The narrow
  `except WorkIQAuthRequiredError` in `resolve_workiq_bearer_token` missed the
  wrapped case, so every agent attach logged a misleading
  `WorkIQ token refresh for agent attach failed` warning **and** fell back to the
  now-expired stored token — which the agent then attached and 401'd on, forcing
  yet another interactive sign-in. We now unwrap the group (reusing
  `_find_in_exception`): a genuine sign-in requirement returns `None` (skip
  attaching WorkIQ, let the keep-alive surface a single re-auth prompt) instead
  of looping on a dead bearer, while genuinely transient transport blips still
  fall back to the stored token and log once.
- **Scheduled `/guard` no longer fails open when WorkIQ needs sign-in.** A guard
  probe against a server parked in `needs_auth` used to "fail open" and run the
  scheduled turn anyway — which then errored because the headless run can't
  authenticate, and never reached the empty/non-empty check (so an empty mailbox
  was never even evaluated). The guard now distinguishes `needs_auth` from a
  transient failure: it surfaces the same inline re-authenticate prompt an
  interactive turn raises (via a new `mcp.auth_required` cross-window event that
  drives the global `McpAuthBanner`), records a durable, de-duplicated note in the
  topic transcript, and skips the run until the user signs in — the next tick
  re-probes for real.
- **A guard skip is now visible on a manual "Run now".** A manual "Run now" on a
  guarded scheduled topic still gates the run (an empty mailbox folder never
  burns an LLM turn), but the skip used to be silent, so the button appeared to
  do nothing. A manual run now records a short note (e.g. "Skipped — the WorkIQ
  guard found nothing to process, so this run didn't start") so you can see the
  gate's verdict. Automatic ticks still skip silently to avoid posting on every
  poll. The auth gate is unchanged: a guard whose server needs sign-in surfaces
  the re-authenticate prompt and skips.
- The Settings endpoint no longer returns **500 Internal Server Error** after
  signing in to the WorkIQ preview (e.g. when toggling Agents mode). The WorkIQ
  OAuth token store wrote its `issued_at` stamp as a raw ISO string into the
  shared `AppSetting` table, but that table's values are all JSON — so
  `_load_all` crashed on `json.loads` of the bare string, taking down every
  read and write of `/api/settings`. The stamp is now JSON-encoded on write and
  decoded on read (with a fallback for legacy raw rows).
- The favicon (and any other top-level file in the SPA build, e.g. assets Vite
  copies from `public/`) is now served in the single-process production build.
  The SPA fallback previously returned `index.html` for everything except
  `/assets/*`, so `/logo.svg` came back as HTML and the browser showed no icon;
  the fallback now serves a real file when the path maps to one inside `dist/`
  (with a traversal guard) and only returns `index.html` for client-side routes.
- Speech-to-text now releases the microphone when dictation stops. The Azure
  Speech SDK's `close()` alone left the OS mic indicator on; Precursor now owns
  the mic `MediaStream` (via `getUserMedia` + `fromStreamInput`) and stops its
  tracks on teardown.
- The `/notes` panel no longer reverts your manual edits after **Rephrase with
  AI**. The rebuilt text was re-applied on every render, so each keystroke
  snapped back to the AI version and the field looked frozen; the suggestion is
  now applied once, when the rephrase returns, leaving it editable.
- Chat errors (provider rejections, the tool-round cap, …) now **stay in the
  transcript** instead of flashing for a few seconds and vanishing. They were
  only added to the transient stream buffer, which was discarded when the
  persisted history reloaded after the stream ended; the error is now persisted
  as a system message.
- `precursor --dev` no longer prints a burst of Vite `http proxy error …
  ECONNREFUSED 127.0.0.1:8000` on startup: the Vite dev server now launches only
  once the backend port is accepting connections, instead of racing it.

- Scheduled topics now actually run: the background scheduler is started (and
  stopped) with the app lifespan. It was constructed but never started, so no
  schedule ever fired and "Run now" was a no-op.
- The `schedules` router is now registered, so `PATCH /api/schedules/{id}`
  (Save) and `POST /api/schedules/{id}/run` (Run now) work instead of returning
  `405 Method Not Allowed` (the requests were falling through to the SPA
  catch-all).

<!--
Release sections are added below by the release process, newest first, e.g.:

## [2026.6.0] - 2026-06-15

### Added
### Changed
### Fixed
### Removed
-->
