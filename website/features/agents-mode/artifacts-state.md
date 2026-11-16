---
title: Artifacts & state
---

# Artifacts & state

**Artifacts** are what an agent publishes for others; **state** is what it
remembers for itself between runs.

## Shared artifacts (blackboard)

A completed run publishes its result as an **artifact**. A
[workflow](/features/workflows) hands a step's artifacts to the following
steps. Artifacts belong to the
[run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)
that published them, so two pipelines sharing an agent don't erase each other's.

An agent publishes a **named** artifact mid-run with a directive:

```text
ARTIFACT: Short value | the content on one line

ARTIFACT: Onboarding guide
# Full Markdown body
...
END_ARTIFACT
```

Use the block form (no pipe, closed by `END_ARTIFACT`) for anything substantial.
Only [autonomous](/features/agents-mode/missions) agents and workflow steps are
taught and parsed for these directives; a plain agent quoting the syntax
publishes nothing.

**Fresh starts wipe the board.** A fresh objective run, a webhook trigger or
`/clear` deletes the previous run's artifacts first. A conversational follow-up
keeps them, which is what turns refinements into versions.

### Reading an agent's result

Once an agent has published something, its page splits into **Result** and
**Activity** tabs (an agent that published nothing keeps the plain timeline).

<Screenshot src="/screenshots/agents-results.png" alt="An agent's Result tab showing version 3 of an onboarding guide, a version rail with v3 (latest), v2 and v1, the follow-up prompt that produced it and the agent's summary of the change" caption="The Result tab opens on the newest version. Earlier ones stay one click away on the rail." />

- **Versions.** Each exchange that publishes adds a version: v1 from the task,
  v2 after your first follow-up, and so on. The tab opens on the newest; a rail
  lists older ones. Each shows the prompt that produced it, the agent's
  `OBJECTIVE_COMPLETE:` summary, and **Show in activity**.
- **Shown once.** In the timeline, the publishing answer shows its prose and a
  compact card (title, version, *latest* / *superseded by vN*) linking to the
  Result tab.
- **Tab follows the work.** Activity while a turn runs, needs you, or stopped
  short; Result once the agent rests on a new version. A tab you click stays put
  until the next turn starts. In Result, the composer asks for changes to the
  version on screen.

With no explicit artifact, the completion summary stands in as the result. The
insights sidebar lists every artifact. Each one can be copied, linked
(`/agents/{id}?artifact={artifactId}`, opens that version) or fetched raw at
`GET /api/agents/{id}/artifacts/{artifactId}/raw`.

## Durable state (the private scratchpad)

For bookkeeping a recurring agent needs next time: the last id processed, a
counter, a cursor.

| | Scope | Survives a re-run? | In the prompt? |
| --- | --- | --- | --- |
| [Memory](/features/skills-memory) | App-wide | Yes | Always injected |
| [Artifacts](#shared-artifacts-blackboard) | One agent run | No | Injected into downstream workflow steps |
| **State** | One agent | **Yes** | **Key index only**; bodies fetched on demand |

Each turn's preamble lists only key names, sizes and timestamps; the agent reads
a body with a tool call, so a large value costs nothing per turn.

| Tool | Does |
| --- | --- |
| `state_list` | List keys |
| `state_get` | Read one value (`found: false` if absent, not an error) |
| `state_set` | Upsert by key |
| `state_delete` | Remove a key |

Tools default to the calling agent's own scratchpad. Limits: values are opaque
text (JSON by convention) up to 100,000 characters, at most 200 keys per agent.
Put large files in a [workspace](/features/workspaces) and store the path. The
insights sidebar's **State** section lists keys, with per-key delete and a
**reset**.

::: tip Exposing state to external MCP clients
In-app agents always have the `state_*` tools. To serve them to outside MCP
hosts, enable **Agent state** under **Settings → MCP servers → Precursor
capabilities**. External callers must pass `agent_id` explicitly.
:::

Workflows have their own
[pipeline state](/features/workflows/steps#pipeline-state-what-a-workflow-remembers),
scoped to the workflow instead of one agent.
