---
title: Configuring a step
---

# Configuring a step

A step's *kind* is chosen when you
[build the pipeline](/features/workflows/building). This page is about
everything else on the step modal: what it's told to do, what it's handed, and
what it's allowed to reach for.

The last two are the cost controls. Tool schemas and upstream transcripts are
re-sent on every turn, so a long pipeline where every step inherits everything
is the expensive default.

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
| <code v-pre>{{run.input}}</code> | The [run brief](/features/workflows/running#the-run-brief-one-workflow-a-different-subject-each-run) for *this* run |
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
is cleared, and a step re-driven by a
[gate loop-back](/features/workflows/building#gates-and-loop-back) resolves to its
latest attempt.

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

It is deliberately *not* the same as an agent's
[own state](/features/agents#durable-state-the-private-scratchpad):

| | Scope | Survives a run? |
| --- | --- | --- |
| Agent state | One agent — which several pipelines may share | Yes |
| Artifacts | One agent **run** | **No** — a new run starts with a clean slate |
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

The override is **snapshotted onto that step's
[agent run](/features/agents#an-agent-is-a-definition-each-start-is-a-run)**, never
written back onto the shared agent. So a step that narrows an agent to "no tools"
doesn't silently disarm the same agent in another pipeline, and the snapshot is
frozen for the life of the run — editing the agent mid-flight primes the next
run instead of changing the one in progress.

### Picking which tool servers a step gets

**Tools: on** still means *every* enabled [MCP server](/features/mcp), and that is
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

A server that's installed *and* enabled but merely **signed out** is a different
case, and it used to be the dangerous one: the session was built without it, the
agent answered the step from its own knowledge, rested idle, and the run recorded
a **success**. A step that named a server stated a hard requirement, so it is now
treated as one — if an allowlisted server can't be attached because its
[OAuth sign-in](/features/mcp) has lapsed, the step parks
**Blocked** naming the server instead of running without it. Sign in, then
**Resume** the run. A step left on **All** is unaffected: it asked for whatever
happens to be available, not for that server in particular.

The name is kept rather than dropped, so an exported workflow imports cleanly
onto a machine with a different server set, and survives the trip back. Import
carries the allowlist verbatim, and the preview warns before you commit to it,
naming the servers this install can't attach and which of the two reasons
applies — so a step doesn't silently run with fewer tools than its author gave
it.

## One voice for the whole pipeline

A workflow can select an **Assistant role**, applied to every step's agent while
the workflow runs. Because agents are shared and reusable, the role is applied at
launch rather than stamped onto the agent rows — so the same `Summariser` can be
formal in one workflow and blunt in another. Leave it unset and each agent keeps
its own role.
