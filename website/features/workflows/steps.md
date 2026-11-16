---
title: Configuring a step
---

# Configuring a step

Everything on the step modal besides its [kind](/features/workflows/building):
what the step is told, what it is fed, and what it may use. The last two are
your main cost controls, since tool schemas and upstream material are re-sent
every turn.

## Per-step instructions

**Step instructions** add a mandate for this stage only, on top of the agent's
objective (and winning where they differ). One `Summariser` agent can be "three
bullets, exec tone" in one pipeline and a long brief in another.

They are offered on steps that reuse an existing agent (an Inline step states its
job in its own prompt). On an approval step they are the brief the reviewer sees.

### Placeholders

| Placeholder | Resolves to |
| --- | --- |
| <code v-pre>{{run.input}}</code> | This run's [brief](/features/workflows/running#the-run-brief-one-workflow-a-different-subject-each-run) |
| <code v-pre>{{step.N.output}}</code> | What 0-based step `N` produced **in this run** (max 8000 characters) |
| <code v-pre>{{state.&lt;key&gt;}}</code> | A value from [pipeline state](#pipeline-state-what-a-workflow-remembers) |

- Add a fallback after a pipe: <code v-pre>{{state.cursor | the beginning of time}}</code>.
  Without one, an unresolved placeholder renders as `(unset)`.
- Placeholders are resolved before the agent sees the text.
  <code v-pre>{{step.N.output}}</code> reads the run trace, so it still works
  after artifacts are cleared, and gives the latest attempt of a re-run step.
- In [definition files](/features/definitions), steps can also be referenced by
  key: <code v-pre>{{step.draft.output}}</code>.
- Pair <code v-pre>{{step.N.output}}</code> with **Context: None** so a step
  gets only the output it names.

::: warning 8000-character ceiling
A longer output is cut and marked in the text
(`… [truncated: 41230 characters, capped at 8000]`). For large payloads, have
the producer publish an [artifact](/features/agents-mode/artifacts-state) and let
the consumer inherit it through its context setting (uncapped). Use the
placeholder for short values: a verdict, a choice, a cursor, a filename.
:::

## Pipeline state: what a workflow remembers

Named values scoped to the **workflow**, shared by all its steps and kept
**across runs**: the place for a cursor ("last invoice processed") that a
scheduled pipeline must not redo.

| | Scope | Survives a run? |
| --- | --- | --- |
| [Agent state](/features/agents-mode/artifacts-state#durable-state-the-private-scratchpad) | One agent, shared by every pipeline using it | Yes |
| Artifacts | One agent run | No |
| **Pipeline state** | **One workflow, all its steps** | **Yes** |

- **Read** with <code v-pre>{{state.&lt;key&gt;}}</code> in step instructions.
- **Write** with the `workflow_state_set` MCP tool (plus `workflow_state_get`,
  `_list`, `_delete`), which targets the workflow running the calling agent.
- Each step's kickoff lists the stored key names (never the bodies). The step
  needs access to the `precursor` MCP server for the tools and the index.
- The **Pipeline state** panel lists values and lets you add, delete or reset them.
  Seed a starting cursor there; reset when a cursor goes bad.

::: warning Bookkeeping, not a blob store
Up to 100,000 characters per value, 200 keys per workflow. Put documents in
artifacts and large files in a [workspace](/features/workspaces) (store the
path).
:::

### A worked example

[`examples/workflows/stateful-digest.yaml`](https://github.com/lrivallain/precursor/blob/main/examples/workflows/stateful-digest.yaml)
(import it from **Workflows → Import**):

1. **Survey since last run** reads <code v-pre>{{state.last_digest_at | the beginning of time…}}</code>.
2. **Write the digest** reads <code v-pre>{{step.0.output}}</code> and
   <code v-pre>{{state.audience | a general technical audience}}</code>, a knob
   you seed from the panel.
3. **Record the cursor** calls `workflow_state_set` for `last_digest_at`.

Read a cursor with a safe default, work relative to it, write the new cursor:
that covers most stateful pipelines.

## What each step is fed

| Context | The step receives |
| --- | --- |
| **Previous step** (default) | The last producing step's output + every earlier step's artifacts |
| **Pick steps** | Only the steps you name. The **highest-numbered** one is the hand-off; the others are reference material |
| **None** | No upstream material; just its objective and the run brief |

The run brief, reviewer notes and the step's instructions are always delivered;
this setting only governs upstream material.

The hand-off is always **this run's**: if the source step produced nothing, the
step is told "produced no output in this run" instead of getting a stale
output. Unticking *Clear each step's artifacts at the start of every run* (a
workflow option) keeps the **artifact board** cumulative across runs on purpose;
the hand-off itself stays run-scoped.

## What each step may use

Each toggle is tri-state: **auto** (inherit the agent's setting), **on**, **off**.

- **Tools**: MCP servers. Turn off for steps that only transform text.
- **Skills**: stored skills.
- **Memory**: long-term memory.

New steps start from the defaults in **Settings → Workflows**. Overrides apply
to that step's [agent run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)
only and are never written back to the shared agent.

### Picking which tool servers a step gets

With tools on, the **Servers** row (listing each server enabled in **Settings →
MCP servers** with its tool count) narrows which servers the step sees. Tool
schemas usually dominate a pipeline's token spend, so name the one or two a
step needs.

| Selection | The step gets |
| --- | --- |
| **All** (default) | Every enabled server |
| One or more servers | Only those |
| Nothing selected | No tool servers (same as **Tools: off**) |

- Unpicked servers are never attached: the step can't call them and pays
  nothing for them.
- Precursor's own server is scoped like the others. Keep it if the step writes
  back to Precursor or uses [pipeline state](#pipeline-state-what-a-workflow-remembers).
- Unknown names are kept and shown struck through (**red** = not installed,
  **amber** = installed but disabled), so exports import cleanly; the import
  preview warns about them.
- An allowlisted server that is enabled but **signed out** (lapsed
  [OAuth](/features/mcp)) parks the step **Blocked**, naming the server. Sign in,
  then **Resume**. Steps on **All** aren't affected.

## One voice for the whole pipeline

Pick an **Assistant role** on the workflow to apply it to every step's agent
during the run, without changing the agents. Leave it unset and each agent keeps
its own role.
