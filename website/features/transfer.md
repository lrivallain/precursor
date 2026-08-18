---
title: Import & export
---

# Import & export

Agents and workflows are **definitions**, not just live objects — so they can
leave the app. **Export** writes one to a plain YAML file you can read, diff,
commit next to the project it automates, or hand to a colleague; **import**
brings one back, asking what to do about anything that already exists here.

::: tip One object per file
An export is always a single top-level object: one agent, or one workflow. A
workflow file still carries the agents its steps reference — a pipeline that
arrives without its agents isn't runnable — but those travel as *dependencies*,
not as co-equal exports.
:::

## Exporting

- **A workflow** — the **download** icon in the detail board header, next to
  **Settings**. The file is named after the workflow (`weekly-digest.workflow.yaml`).
- **An agent** — **Export as YAML** in the agent's settings drawer, above
  **Archive agent**. Step-private agents can't be exported on their own; export
  the workflow that owns them.

### What travels

Everything that describes *what to run*: the prompt and objective, the model,
autonomy and step budget, the approval policy, token budget and retries, the
capability toggles (MCP, skills, memory), the [Assistant Role](/features/skills-memory#assistant-roles)
persona, and — for a workflow — its full step wiring, including gate loop-back
targets, approval checkpoints, per-step instructions, context sourcing and
capability overrides.

### What deliberately doesn't

| Left out | Why |
| --- | --- |
| Status, run history, artifacts, token counters, progress | A file describes what to run, never what happened. |
| The Copilot SDK session handle | A runtime resume handle for *this* install; it means nothing elsewhere. |
| **Webhook tokens** (workflow and agent triggers) | They're per-install credentials. Sharing a file must never share a way to fire it. Re-mint on the target. |
| Topic / chat links | Machine-local containers. An imported agent arrives unattached. |

Because the recurrence *is* part of the definition, a schedule travels — but it
arrives **paused**. A file dropped into a new install describes *when* something
should run; it doesn't get to start running it. Precursor says so in the import
preview, and you arm it deliberately afterwards.

## Importing

**Import** sits next to **New workflow** in the Workflows gallery and next to
**New agent** on the Agents dashboard. Drop a `.yaml` file (or pick one) and
Precursor shows you what it *would* do before writing anything.

### Resolving what already exists

The interesting case is a name that already exists here. That's a genuine fork
in the road, so the preview asks per agent:

| Choice | What happens |
| --- | --- |
| **Use existing** | The imported step points at the agent you already have, left completely untouched — prompt, history and all. |
| **Replace** | The existing agent's definition is overwritten **in place**. It keeps its id, so every *other* workflow already using it picks the new definition up. The preview warns you how many that is. |
| **Create new** | Both are kept: the file's agent is imported as a separate copy under a disambiguated name (`Reviewer (2)`). |

A workflow whose own name collides offers **Replace** or **Create new** — a
workflow isn't a shared resource the way an agent is, so "use existing" has no
meaning for it.

### Matching: identity before names

On first export, an object is stamped with a stable portable id that travels in
the file. So re-importing a file that originally came from **this** install
recognises the very object it came from, rather than guessing from the name —
the preview says *"this is the same one you exported"* and defaults to
**Replace**, which makes round-tripping an edited file the obvious path.

A bare **name** match is a much weaker signal, so it defaults to **Use existing**
instead: a shared file must never silently overwrite someone's prompt. That
default also governs scripted imports that send no choices at all, which are
therefore always non-destructive.

::: warning Replace ripples
Agents are shared. Replacing one edits the definition every live workflow
referencing it will use on its next run. The preview surfaces the count up
front, but it's worth a second look when it's more than one.
:::

### Personas and inline steps

- A carried **Role** is matched by name and only created when it's genuinely
  new, so importing two workflows that share a persona converges on one role
  rather than accumulating copies.
- **Inline step agents** — a step's private vessel — are never conflict-checked.
  They belong to their step, not to the roster, so they're simply recreated with
  it.

### When the target install is different

A pinned **model** that this install doesn't offer is reported as a warning, not
an error: the runtime may simply be down, and the agent still imports fine — it
falls back to the default model at run time.

## API surface

| Method & path | Purpose |
| --- | --- |
| `GET /api/transfer/workflows/{id}` | Download a workflow (+ its agents) as YAML |
| `GET /api/transfer/agents/{id}` | Download an agent as YAML |
| `POST /api/transfer/preview` | `{ "content": "<yaml>" }` → conflicts + warnings. Writes nothing |
| `POST /api/transfer/import` | `{ "content": "<yaml>", "resolutions": [...] }` → applies it |

A resolution is `{ "kind": "agent" \| "workflow", "index": <n> \| null, "action":
"replace" \| "create" \| "link" }`, keyed exactly as the preview reported the
conflict. Anything left unresolved falls back to the preview's safe default.

## The file format

```yaml
format: 1
kind: workflow
exported_by: Precursor 2026.1
exported_at: '2026-08-13T10:24:11+00:00'
agents:
  - export_id: 2f1c…            # stable portable identity
    title: Collector
    task: Gather this week's issues
    use_mcp: true
    role:
      name: Terse editor
      system_prompt: Be brief.
  - title: Review               # an inline step's private vessel
    task: Check the draft reads well
    inline: true
workflow:
  export_id: db19…
  name: Weekly digest
  description: Collect, review, publish
  max_loops: 3
  steps:
    - agent: 0                  # index into `agents`, never a local database id
      name: Collect
      kind: task
    - agent: 1
      kind: gate
      on_fail_position: 0
    - name: Publish?
      kind: approval
      on_reject: stop
```

Steps address agents **by index within the file**, because database ids are
install-local — which also keeps the document readable and editable by hand.
`format` is refused when it's newer than the running version, rather than
silently dropping fields it doesn't understand.
