---
title: Artifacts & state
---

# Artifacts & state

Two different stores, deliberately kept apart: **artifacts** are what an agent
*publishes* for others to read, and **state** is what it *remembers* for itself
between runs.

## Shared artifacts (blackboard)

Agents share results through a **blackboard**. A completed run **publishes** its
result as an **artifact**, and a [workflow](/features/workflows) hands a step's
artifacts to the next step's kickoff context — this is the channel that turns a
series of agents into a pipeline. Artifacts belong to the
**[run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)**
that published them, so a second pipeline sharing the same agent never erases a
blackboard the first one is still reading.

An agent can also publish a **named** output mid-run with an `ARTIFACT:`
directive, in one of two shapes:

- **Inline** — `ARTIFACT: <title> | <content>` on one line, for a short value.
- **Block** — `ARTIFACT: <title>` with **no pipe**, then the full Markdown body
  on the following lines, closed by `END_ARTIFACT`. Use this for a substantial
  deliverable so it lands whole instead of being truncated to a heading.

Published artifacts are rendered as **end-of-turn deliverables** at the foot of
the conversation and listed in the insights sidebar. Each is addressable: copy
its content, copy a **permalink** (`/agents/{id}?artifact={artifactId}`), or
**open raw** at `GET /api/agents/{id}/artifacts/{artifactId}/raw`.

**The blackboard is freshened, never stacked.** Anything that starts the agent
*over* — a fresh objective re-run, a webhook re-trigger, or **`/clear`** — wipes
the previous run's artifacts first. A conversational follow-up is the exception:
it keeps them, because it's continuing the run rather than restarting it.

## Durable state (the private scratchpad)

Artifacts are for **publishing**; state is for **remembering**. A recurring agent
needs to know where it got to last time — the last id it processed, a counter —
and neither of the other two stores fits:

| | Scope | Survives a re-run? | In the prompt? |
| --- | --- | --- | --- |
| [Memory](/features/skills-memory) | Global, app-wide | Yes | **Always injected**, everywhere |
| [Artifacts](#shared-artifacts-blackboard) | One agent | **No** — wiped on each fresh run | Injected into downstream fleet agents |
| **State** | One agent | **Yes** | **Keys only** — bodies on demand |

**The bodies never enter the prompt.** Each turn the agent's preamble gets only a
**key index** — names, sizes, and when they changed — and the agent pulls the one
body it needs with a tool call. That's the whole point: a 40 KB saved cursor
costs nothing per turn.

Agents read and write it through four first-party MCP tools, which default to the
calling agent's own scratchpad: `state_list` (keys only), `state_get` (returns
`found: false` on a first run rather than an error), `state_set` (upserted by
key) and `state_delete`.

Values are opaque text (JSON by convention) capped at 100 KB, with at most 200
keys per agent. **Keep bodies small** — this is bookkeeping, not payload storage.
For anything large or worth version-controlling, write the file to a
[workspace](/features/workspaces) and store just the path here. The insights
sidebar lists the keys under **State** and offers a per-key delete plus a
**reset** for when a saved cursor has gone bad.

::: tip Exposing state to *external* MCP clients
The `state_*` tools are always available to Precursor's own agents. Serving them
to outside MCP hosts is a separate, opt-in disclosure — enable **Agent state**
under **Settings → MCP servers → Precursor capabilities**. External callers must
also name the `agent_id` explicitly; only an in-app agent gets the implicit "me".
:::

A [workflow](/features/workflows/steps#pipeline-state-what-a-workflow-remembers)
has its own equivalent, scoped to the pipeline rather than to one agent.
