---
title: Agents
---

# Agents

**Agents mode** hands a long-running task to an autonomous **Copilot SDK** agent
attached to a topic or chat, then lets it run in the **background** while you
monitor the whole fleet from a **control-tower dashboard**. It's **opt-in and off
by default**.

<Screenshot src="/screenshots/agents.png" alt="An agent session timeline showing the task, a reasoning step, and the assistant's answer with suggested replies" caption="An agent session — the task at the top, then a workflow-style timeline: reasoning, tool calls, and the assistant's answer with suggested follow-ups." />

## Enabling agents

Agents mode isn't part of the default install — it lives behind an `agents`
extra that bundles the native Copilot CLI runtime:

```bash
uv sync --extra agents                 # adds github-copilot-sdk
uv run --extra agents precursor --dev  # …or run the dev stack with it
```

Installing the extra *is* the opt-in: with no stored preference, Agents mode
follows the runtime, so it comes on as soon as the CLI resolves on your platform.
**Settings → Agents** is the one control on top of that — turn it off there to
stop the runtime without uninstalling anything, and the panel reports whether the
runtime resolved.

Without the extra the probe fails, agents stay off, and Settings tells you which
install command to run.

::: tip Recovering a stalled runtime
Settings distinguishes *available* (the runtime is installed and resolves) from
*started* (it actually came up in this process). If the SDK client fails to
launch — most often after a dev auto-reload interrupts a turn — Settings shows a
warning that the runtime is installed but didn't start; restart Precursor to
recover. On every boot, any agent left mid-turn is reset to **Interrupted** so
you can **Resume** it rather than leaving it silently pinned in `running`.
:::

::: warning ~90 MB native runtime
The `github-copilot-sdk` wheel bundles the native runtime binary (~90 MB
download, ~145 MB on disk) — which is exactly why it's an opt-in extra rather
than a default dependency. See [installation](/guide/installation#optional-agents-mode).
:::

## The agent dashboard

Agents are meant to run in the **background** — you kick one off and let it work,
you don't babysit its timeline. So opening Agents mode doesn't drop you into a
single run; it lands on a **control-tower dashboard** for the whole fleet.

- A row of **KPI stat tiles** across the top counts agents in each lane — **Need
  you**, **Working**, **Idle / done**, and **Scheduled** — each a big tabular
  number that dims when empty; the **Need you** tile glows amber when it's
  non-zero and the **Working** tile spins while agents are live. Each tile also
  **doubles as a filter**: click one to narrow the board to that lane, click it
  again to clear. A **New agent** button sits on the right.
- A **search box** in the header filters the board by **agent name** as you
  type. It stacks on top of the tile filter, so you can look for a name *within*
  "Needs you"; a chip above the lanes says what's active and clears both in one
  click.
- Below the tiles, the monitor cards are grouped into **urgency swimlanes** —
  **Needs you**, **Working**, and **Idle · done** — so the agents that want you
  float to the top and quiet ones settle underneath. Empty lanes are hidden, and
  each lane header carries a colored dot and its own count.
- Each **monitor card** shows its [status medallion](#status-at-a-glance), status
  pill, live tool, next scheduled run, unread replies, linked topic/chat, and
  model. Cards lift on hover and carry a left status-rail in their lane color;
  the ones needing you get a warm gradient wash.

The dashboard is the default view whenever you have at least one agent and
haven't opened a specific run. Click any card to drop into that agent's
[timeline](#following-a-run); hit **New agent** to start a fresh task.

### No parallel session list

Agents mode deliberately **drops the left session list** that topics and chats
use — a list of open agents next to an open agent just made them feel like
"alternative topics". Instead, the dashboard *is* the list, and a **slim icon
rail** stays on the left only to switch between sections (Topics, Chats,
Agents, …). When you open a single agent, its header grows an **← All agents**
button that returns you to the dashboard; re-clicking the **Agents** rail icon
does the same. Per-agent actions (rename by double-clicking the title, plus
archive, stop, and delete) live in that same header.

### The attention router

Cards are **urgency-sorted**, not chronologically: an agent **waiting on you** —
a parked approval or a raised **Needs input** question — floats to the very top,
then interrupted/failed runs, then live work, then quiet idle/completed states.
Within a lane, agents with unread replies and more recent activity sort first.
The ordering is shared by every surface (dashboard and command palette), so
"what needs me next" is always the top row wherever you look.

### Status at a glance

Every surface — the dashboard, the command palette, an open agent's header —
renders the same **status medallion**: a coloured dot that **pulses** while the
agent is working, wears an **amber ring** when it needs you, sits **slate** when
it's [parked `waiting`](#park-an-agent-until-a-trigger) for a trigger, and grows
a small **fan-out cluster badge** when the agent is running tool calls in
parallel (its "sub-agents"). One glyph, the same meaning everywhere.

### Live activity

While an agent is working, its dashboard card shows the **current tool** it's
running (e.g. a file read, a shell command) and, when several run at once, a
`×N parallel` count. This is derived live from the agent's in-process event
stream, so you can see momentum without opening the run. Agents that aren't live
in this process simply omit the line.

Alongside the tool, the card surfaces the agent's own **live narration** — the
first plain-language line of the assistant message it's currently streaming,
stripped of control directives and markdown. It renders as a muted italic line
under the tool chip (or a standalone chip when no tool is running), and the icon
rail folds it into the row label + tooltip. So a backgrounded agent reads as
"what it's doing now" in its *own words* rather than a bare tool name, and falls
silent between turns.

## Orchestrating agents

A single agent is a run; several of them make a **fleet** you watch from one
dashboard. Precursor lets agents **park** until triggered, **budget** and
**retry** themselves, share results on a **blackboard**, keep **durable state**
between runs, and fire on external **events** — with a fleet-wide concurrency cap
over the lot.

::: tip Chaining agents into a pipeline
Sequencing one agent after another — research → draft → review — is owned by
**[Workflows](/features/workflows)**, a reusable coordinator that runs a series
of agents in order and passes each step's output to the next. Individual agents
stay independent; a workflow supplies the chaining.
:::

### An agent is a definition; each start is a run

An agent is a **reusable definition** — a title, an objective, a model, a role
and its capability defaults. Every time something starts it, Precursor opens a
separate **run**: its own status, prompt, transcript, artifacts, token meter and
Copilot SDK session.

That split is what makes an agent **safe to share**. Two [workflows](/features/workflows)
can point a step at the same agent and both be in flight at once — each drives
its own run, so neither sees the other's status, wipes the other's artifacts,
inherits the other's tool grants or is charged for the other's tokens. Before
runs existed, the second pipeline to start quietly took over the first one's
execution.

A run records **what triggered it**, so the history reads as an audit trail:

| Trigger | Opened by |
| --- | --- |
| `manual` | you, from the agent's header or **Start now** |
| `workflow` | a [workflow](/features/workflows) step reaching the agent |
| `schedule` | a [scheduled](#scheduling-agents) tick |
| `webhook` | an [event trigger](#event-triggers-webhooks) firing |
| `fleet` | the concurrency governor releasing a queued agent |
| `retry` / `replay` | a re-attempt of a workflow step |

Runs are kept, never garbage-collected. The agent's insights sidebar carries a
**Runs** rail listing the most recent ones — trigger, status, the workflow run
that drove it, and what it spent — with the current run highlighted. In a
workflow's [step trace](/features/workflows/running#run-history-and-the-step-trace),
each row names the run it launched, so you can walk from a pipeline step to the
exact execution behind it.

**The transcript shows the latest run by default**, and the rail doubles as a
filter: click any run to read that execution on its own, or **All runs** to
stitch the whole history back together. A chip above the timeline says which run
you're reading. Until you pick one yourself the view follows the newest run, so
starting the agent again pulls the transcript along instead of leaving you on a
finished conversation.

This matters most for a shared agent: two workflows driving it at once produce
one interleaved archive — two prompts and two answers with nothing saying which
belongs to which — and the per-run view is what pulls each conversation back
apart.

::: tip
A handful of Precursor-generated notices — the "this MCP server needs
authorising" banner, for instance — belong to the agent rather than to any one
run, so they only show in the unfiltered view.
:::

The agent row itself keeps mirroring its **current** run's status and counters,
which is what the dashboard cards, the inbox and the command palette read — so
"what is this agent doing right now" stays a single cheap lookup.

### Park an agent until a trigger

Not every agent should run the moment you create it. The composer's **Create
parked (don't run yet)** toggle arms an agent in the **`waiting`** state instead
of starting it: it's fully configured but idle until something triggers it —

- its **[webhook](#event-triggers-webhooks)** firing,
- a manual **Start now** button on the agent's detail view, or
- a **[workflow](/features/workflows)** step reaching it.

A parked agent is never picked up by the orphan sweep — only an explicit trigger
launches it — so you can pre-stage a run and fire it when you're ready. Under the
hood this is a `start: false` flag on agent creation plus a
`POST /api/agents/{id}/start` endpoint.

### Budgets & the concurrency governor

- **Concurrency governor.** A fleet-wide cap (`agents_max_concurrent`, default 3)
  limits how many agents run at once; the rest queue and are released as slots
  free up, so a burst of new agents can't stampede the runtime.
- **Token budget (per agent).** Give an agent a **token budget** in its settings
  drawer and, once its cumulative usage crosses it, the agent **parks itself in
  the [inbox](#the-unified-inbox)** for your approval instead of spending more —
  a hard ceiling on a runaway run. Leave it blank for unlimited. The budget is
  **cumulative governance on the definition**: spend from *every*
  [run](#an-agent-is-a-definition-each-start-is-a-run) counts against it, so
  restarting the agent doesn't hand it a fresh allowance, and two concurrent
  runs draw on the same pot. The run that trips the cap is the one that parks.

### Retry & auto-recovery

Set **max retries** on an agent and a failed run is **automatically retried**
with **exponential backoff** (`agents_retry_backoff_seconds` × 2ⁿ) before it's
finally marked failed. The scheduler scans for due retries every tick, so a
transient error recovers itself without you noticing.

### Blueprints (reusable templates)

A **blueprint** saves a task prompt plus a governance profile (model, approval
policy, token budget, retries) as a reusable template. Manage them under
**Settings → Agents → Blueprints**; hit **Run** on one to **stamp out a fresh
agent** with the same profile. Blueprints keep a repeatable job (a nightly triage,
a standard research pass) one click away instead of retyped.

### Shared artifacts (blackboard)

Agents share results through a **blackboard**. A completed run **publishes** its
result as an **artifact** (deduped so identical results don't pile up). A run's
insights sidebar lists the artifacts it published, and a
**[workflow](/features/workflows)** hands a step's artifacts to the next step's
kickoff context — this is the channel that turns a series of agents into a
pipeline.

Artifacts belong to the **[run](#an-agent-is-a-definition-each-start-is-a-run)**
that published them, not to the agent. Starting an agent clears only *its own*
new run's slate, so a second pipeline sharing the same agent never erases a
blackboard the first one is still reading from.

An agent can also publish a **named** output on purpose, mid-run, with an
`ARTIFACT:` directive (repeatable). Use it to hand a specific, structured result
(a decision, a config block, a shortlist, a draft) to a later workflow step or a
watcher, rather than relying on the auto-captured completion summary. There are
two shapes:

- **Inline** — `ARTIFACT: <title> | <content>` on one line, for a short value.
  Because the whole directive is one physical line, the model writes `<content>`
  as **Markdown** and uses `\n` for line breaks; the backend unescapes those
  (and, as a safety net, breaks a numbered list packed onto one line back onto
  separate lines) so lists and paragraphs render properly.
- **Block** — for a **substantial or multi-line deliverable**, the model puts the
  title on the `ARTIFACT:` line with **no pipe**, then the full Markdown body on
  the following lines, closed by an `END_ARTIFACT` line (or the next directive /
  end of message). This is what lets an inventory, a draft, or a review land
  **whole** instead of the model stranding the real content in prose and
  capturing only a heading.

A trailing `PROGRESS`/`OBJECTIVE_COMPLETE` line a model accidentally glues onto
the body is stripped off. Like the other directives the marker lines are removed
from the raw prose; the output is then surfaced **once**, as an end-of-turn
deliverable (see below) and listed in the insights sidebar — so it's visible in
the discussion *and* is available to a later workflow step, without repeating
inline.

**Deliverables render inline in the discussion flow — as a single, non-duplicated
answer.** The published artifacts are the run's *expected output*, so they're
rendered at the foot of the conversation **unboxed, in the normal discussion
background**: a horizontal rule slips each one off from the streamed prose, a
quiet title row labels it, then the body renders as plain **Markdown** (Markdown
passes through, JSON in a fenced block, a `link` as a real anchor) — so it reads
as the agent's answer to your request, not a card. The copy / copy-link /
open-raw actions surface on hover. To avoid saying the same thing three times
(prose, completion, deliverable), the section prefers the model's **explicit
`ARTIFACT:` outputs** and falls back to the **auto-captured completion summary**
only when nothing explicit was published — since that summary is already shown in
the *Objective complete* answer bubble. This is the human-facing surface; the
insights sidebar keeps the same list as a compact index that a workflow can feed
into a later step's kickoff context.

**Every artifact is addressable.** Click a sidebar entry (or open a deliverable's
permalink) to open a viewer that renders it by kind (Markdown, pretty-printed JSON,
plain text, or a clickable link) with three affordances:

- **Copy content** — the raw payload to your clipboard.
- **Copy link** — a shareable permalink (`/agents/{id}?artifact={artifactId}`)
  that reopens the agent with this artifact auto-expanded.
- **Open raw** — the artifact's body served on its own at
  `GET /api/agents/{id}/artifacts/{artifactId}/raw` with a kind-appropriate
  content-type (`text/plain`, `text/markdown`, `application/json`), for
  download or programmatic access. A `link` artifact redirects straight to its
  URL.

**The blackboard is freshened, never stacked.** Anything that starts the agent
*over* — a fresh objective re-run, a webhook re-trigger, or clearing the agent's
context with **`/clear`** — first **wipes the previous run's artifacts** so the
new turn's deliverables replace the old ones instead of piling up next to stale
output. (A conversational **follow-up** in the same context is the exception — it
*keeps* the existing artifacts, because it's continuing the run, not restarting
it.) The clear re-broadcasts the agent, so the sidebar and in-chat deliverables
empty on their own without a manual reload.

### Durable state (the private scratchpad)

Artifacts are for **publishing**; state is for **remembering**. A recurring agent
needs to know where it got to last time — the last id it processed, the items it
already saw, a counter — and neither of the other two stores fits:

| | Scope | Survives a re-run? | In the prompt? |
| --- | --- | --- | --- |
| [Memory](/features/skills-memory) | Global, app-wide | Yes | **Always injected**, everywhere |
| [Artifacts](#shared-artifacts-blackboard) | One agent | **No** — wiped on each fresh run | Injected into downstream fleet agents |
| **State** | One agent | **Yes** | **Keys only** — bodies on demand |

So state is the home for cross-run bookkeeping, and it's what a
[scheduled](#scheduling-agents) or [webhook-triggered](#event-triggers-webhooks)
agent uses to resume instead of redoing work.

**The bodies never enter the prompt.** Each turn, the agent's preamble gets only
a **key index** — the key names, their sizes and when they changed. The agent
then pulls the one body it actually needs with a tool call. That's the whole
point: a 40 KB saved cursor costs nothing per turn, which is exactly what putting
it in long-term memory *would* cost.

Agents read and write it through four first-party MCP tools, which default to the
calling agent's own scratchpad:

| Tool | What it does |
| --- | --- |
| `state_list` | List saved keys (**keys only**, no bodies). |
| `state_get` | Read one entry. Returns `found: false` on a first run rather than an error. |
| `state_set` | Save/replace one entry — upserted by key, so a re-run overwrites rather than accumulating. |
| `state_delete` | Drop one entry. |

Values are opaque text (JSON by convention) capped at 100 KB, with at most 200
keys per agent. **Keep bodies small** — this is bookkeeping, not payload storage.
For anything large, binary, or worth version-controlling, write the file to a
[workspace](/features/workspaces) and store just the path here.

The insights sidebar lists the saved keys under **State**, expands one to show its
body, and offers a per-key delete plus a **reset** that wipes the scratchpad — the
operator's lever when an agent's saved cursor has gone bad. State is deleted with
its agent, and is reachable over HTTP at `GET|PUT /api/agents/{id}/state`.

::: tip Exposing state to *external* MCP clients
The `state_*` tools are always available to Precursor's own agents. Serving them
to outside MCP hosts is a separate, opt-in disclosure — enable **Agent state**
under **Settings → MCP servers → Precursor capabilities**. External callers must
also name the `agent_id` explicitly; only an in-app agent gets the implicit "me".
:::

### Event triggers (webhooks)

Beyond schedules and completion edges, an agent can be re-run by an **external
event**. Add a **webhook** from the insights sidebar to mint a unique URL
(`POST /api/agents/hooks/{token}`); calling it re-runs that agent. Copy or delete
the URL from the same panel. Bad or disabled tokens 404 (no enumeration).

### The unified inbox

Everything waiting on you — parked **approvals**, raised **Needs input**
questions, and **budget parks** — is collected into one **inbox** rendered as a
strip across the top of the dashboard. Each chip is colour-coded by kind and
deep-links to the agent, so "what's blocked" is one list rather than a hunt
across cards. It's backed by `GET /api/agents/inbox`.

### Aggregate observability

The dashboard's header also shows a **fleet rollup** — how many agents are
running vs queued and the total tokens consumed against the sum of budgets —
from `GET /api/agents/metrics`. It's the whole-fleet counterpart to a single
run's usage panel.

## Autonomous missions

By default an agent runs a **single turn** and comes to rest — you hand it a
task, it answers, and it waits for your next message. Flip **Run autonomously**
on when you start it (off by default) and the task becomes a **durable
objective**: the agent pursues it on its own, **continuing between turns** and
pulling you in only when it finishes or gets stuck.

- **Objective, not a one-shot prompt.** The task you type becomes the agent's
  standing objective. A **mission strip** at the top of the timeline (and the
  card's `Auto` pill) keeps it in view along with a `step N/max` counter.
- **The goal loop.** Each time the agent goes idle, Precursor reads the tail of
  its reply for a control directive and decides what happens next — hand back,
  stop, or **keep going** by nudging itself with another step. No repost fires
  between steps, so a multi-step mission lands as **one** result in the linked
  topic/chat at the end, not a burst of intermediate turns.
- **Self-reported progress.** When the agent emits a `PROGRESS:` directive, its
  card and mission strip render a **progress bar** with the percentage and a
  short label — the fleet-level sense of "how far along is it". The same
  heartbeats are also lifted out of the prose and re-drawn as compact,
  iconified **milestone nodes on the transcript spine** (a violet `%`-plus-label
  pill per heartbeat); the terminal **Objective complete** state is folded into
  the final answer bubble rather than repeated as its own node, so the
  mission's trajectory reads as a timeline as you scroll rather than as raw
  `PROGRESS:` / `OBJECTIVE_COMPLETE:` lines buried in the text.
- **Blocked, not silently looping.** If the agent needs a decision it emits
  `NEED_INPUT:` and moves to a **Needs input** state (warm amber, floated to the
  top of the dashboard next to approvals). The directive markers are **stripped
  from the transcript** and the raised question is re-surfaced as its own amber
  **"Needs your input"** callout inside the message — plus a banner at the top —
  each with an **Answer** button that jumps you straight to the reply
  box. So the one line you must act on never hides in prose; your reply resumes
  the mission. It also self-blocks if it **stalls**
  (no progress for several steps) or **spends its step budget**, so it can't spin
  forever.
- **Step budget.** A per-agent **step budget** (default 12, 1-100) caps how many
  times it may continue before handing back — the primary guardrail on an
  autonomous run.

### The autonomy protocol

An autonomous agent is told to end a reply with a lifecycle directive; the
goal loop parses them (case-insensitive, last match wins). The **cadence** of
these directives lives in the durable system preamble (`_AUTONOMY_PROTOCOL`), so
every autonomous run behaves consistently for the dashboard: narrate one short
plain sentence before each action, emit `PROGRESS` several times across the run
(early / middle / late, not only at the end), publish an `ARTIFACT` as each
phase or finding lands, and close with a 2–3 sentence `OBJECTIVE_COMPLETE`. A
mission prompt can still layer on task-specific specifics (named phases, exact
checkpoint percentages) — the durable layer sets the *floor*, the task sets the
*shape*.

| Directive | Effect |
| --- | --- |
| `OBJECTIVE_COMPLETE: <summary>` | Mission done — status **Completed**, summary saved, progress forced to 100%, single repost to the linked surface. Folded **into the final answer bubble** as an **Objective complete** badge (and, when the turn's prose was entirely directives, the summary becomes that bubble's body) — so the completion and the answer are one and the same, not two nodes saying the same thing. |
| `NEED_INPUT: <question>` | Agent is blocked — status **Needs input**, the question surfaced for you; your next message unblocks it. |
| `PROGRESS: <0-100> \| <label>` | Optional heartbeat alongside either of the above (or a bare continuation) — updates the progress bar, drops a **milestone node** on the spine, and resets the stall counter. |
| `ARTIFACT: <title> \| <content>` | Optional, repeatable — publishes a named output to the shared [blackboard](#shared-artifacts-blackboard) for a later workflow step or a watcher. **Inline** (`<title> \| <content>`) is one-line **Markdown** (use `\n` for line breaks; the backend unescapes them and un-packs an inline numbered list). For a substantial deliverable use the **block** form — `ARTIFACT: <title>` with no pipe, then the full body on the next lines, closed by `END_ARTIFACT` — so nothing is truncated. Surfaced once as an **end-of-turn deliverable** (rendered as Markdown) and listed in the insights sidebar. Doesn't change the run's lifecycle. |

All four marker lines are **stripped from the rendered transcript** and
re-surfaced as structured UI (progress bar + progress **milestones** on the
spine, the terminal **Objective complete** badge folded into the answer bubble,
the amber **Needs your input** callout, and end-of-turn **deliverables** that
also feed the sidebar artifacts list) so the prose stays clean.

If none of these appears and the budget/stall guards allow it, the agent
**continues autonomously** to the next step. Plain (non-autonomous) agents ignore
the protocol entirely and rest at **Idle** after each turn, exactly as before.

::: warning Experimental
Autonomous missions are new and best kept on a **step budget** with permission
guards in place. Watch a mission's first few runs before trusting it unattended.
:::

## Following a run

Give an agent a **task prompt** and it works autonomously, streaming its steps
into a **timeline** you can watch. Long runs are windowed client-side so even a
very long transcript doesn't mount thousands of nodes at once.

- **Tool calls** are visualized inline, so you can see what the agent did.
- **Permissions** — the agent surfaces permission prompts for actions that need
  your approval.
- **Usage** — token/usage accounting is tracked per session.

### Approval policy (per agent)

Every agent action is gated by an **approval policy**. There's a global default
in **Settings → Agents** (`manual` asks before every action, `balanced`
auto-approves read-only tools, `autonomous` approves everything), but each agent
can **override it** — pick a policy when you start the agent, or change it later
from the agent's **settings drawer**.

- **Inherit (default).** Leave it on *Inherit global default* and the agent
  follows whatever Settings says — change the global and every inheriting agent
  moves with it.
- **Per-agent override.** Set `manual` / `balanced` / `autonomous` on one agent
  to run a trusted mission more freely (or a risky one more cautiously) without
  touching the fleet-wide default.
- **Takes effect next turn.** The policy is read at the start of each turn, so
  switching it is a plain field change — **no session rebuild**, unlike editing
  the objective or role.

### Editing an agent (save vs. run)

The **settings drawer** separates *persisting* changes from *acting* on them:

- **Save** only writes your edits. Changing the **objective** or **role** *primes*
  the new instructions — the cached SDK session is dropped so the next run picks
  them up — but **saving never launches a turn**. A running or waiting agent keeps
  doing what it was doing.
- **Save & run** persists the same edits and then **starts the objective now**,
  clearing the previous run's artifacts before replaying the (freshly saved) task.
  It's disabled while the agent is already active — stop it first.

This makes editing safe to do at any time: tweak the prompt, governance, or
schedule and hit **Save**, then choose *when* to re-run.

Editing is also safe **while a run is in flight**. A run snapshots the model,
role, approval policy and capability toggles it started with, so changing the
definition mid-flight primes the *next* run rather than moving the ground under
the current one.

## Unread badges & notifications

Agent sessions track unread activity just like topics and chats. When a
background or scheduled agent produces a new reply while you aren't looking, its
dashboard card is highlighted with an unread count, and — when notifications are
enabled and the window is unfocused — a browser notification fires. Opening the
session clears its badge.

### The "agent needs you" signal

Background agents pause when they hit an action that needs your approval, and the
whole point of running them in the background is that you're *not* watching. So
that block is surfaced **out of band**, the moment it happens:

- A **browser notification fires regardless of focus** (not only when the window
  is hidden) the instant an agent transitions to **Needs approval**, and clicking
  it deep-links straight to that agent. It respects the same notifications toggle
  as unread replies.
- The **browser tab title** grows a 🔔 bell and a `(n)` count whenever any agent
  is waiting on you, so a backgrounded Precursor tab advertises "an agent needs
  you" without being focused.
- The **⌘K command palette** lists the agents that need attention **first**,
  urgency-sorted, so the keyboard path to an unblock is always the top result.

Open an agent to manage it: double-click its title to rename, or use the header
actions to archive, stop, or delete the session.

## Scheduling agents

An agent can carry its **own recurrence** (an `AgentSchedule`) so it re-runs its
stored task on a cadence — the first-class equivalent of a scheduled topic that
nudges an agent. Each due tick either replays the task on a **fresh transcript**
(`clear_context`) or sends a **follow-up** in the existing conversation. A run is
skipped (not failed) while the agent is mid-turn, **waiting on you** (needs
approval or blocked on a question), archived, or task-less.

You can also drive an agent from a scheduled topic with slash directives:

- `/agent <uuid> /clear <follow-up>` — reset the transcript (same uuid) and
  freshen the [blackboard](#shared-artifacts-blackboard), then send a follow-up.
- `/agent <uuid> /run [extra]` — reset, then replay the agent's own task prompt
  (plus an optional one-off extra), keeping instructions in one place.

See the [scheduler](/features/scheduler) for how these directives — and `/guard`
gating — fit together.

## Sharing an agent

An agent's definition — prompt, model, persona, budgets and cadence — can be
**exported to a YAML file** from its settings drawer and imported into another
install. What travels is the definition only: no run history, and no webhook
tokens. See [import & export](/features/transfer).
