---
title: Import & export
---

# Import & export

Agents and workflows are **definitions**, not just live objects — so they can
leave the app. **Export** writes one to a plain YAML file you can read, diff,
commit next to the project it automates, or hand to a colleague; **import**
brings one back, asking what to do about anything that already exists here.

<Screenshot src="/screenshots/transfer.png" alt="The Workflows gallery with two pipeline cards and an Import button in the header" caption="Import sits beside New workflow on the gallery; every card can be exported from its detail board." />

::: tip One object per file
An export is always a single top-level object: one agent, or one workflow. A
workflow file still carries the agents its steps reference — a pipeline that
arrives without its agents isn't runnable — but those travel as *dependencies*,
not as co-equal exports.
:::

## Exporting

- **A workflow** — the **download** icon in the detail board header.
- **An agent** — **Export as YAML** in the agent's settings drawer. Step-private
  agents can't be exported on their own; export the workflow that owns them.

Everything that describes *what to run* travels: the prompt and objective, the
model, autonomy and step budget, the approval policy, token budget and retries,
the capability toggles, the [Assistant Role](/features/skills-memory#assistant-roles)
persona, and — for a workflow — its full step wiring, including gate loop-back
targets, approval checkpoints and per-step instructions.

### What deliberately doesn't

| Left out | Why |
| --- | --- |
| Status, run history, artifacts, token counters | A file describes what to run, never what happened. |
| The Copilot SDK session handle | A runtime resume handle for *this* install; it means nothing elsewhere. |
| **Webhook tokens** | They're per-install credentials. Sharing a file must never share a way to fire it. Re-mint on the target. |
| Topic / chat links | Machine-local containers. An imported agent arrives unattached. |

Because the recurrence *is* part of the definition, a schedule travels — all of
its [rules](/features/scheduler#several-cadences-on-one-item), not just the
first — but it arrives **paused**. A file dropped into a new install describes
*when* something should run; it doesn't get to start running it.

## Importing

**Import** sits next to **New workflow** in the Workflows gallery and next to
**New agent** on the Agents dashboard. Drop a `.yaml` file and Precursor shows
you what it *would* do before writing anything.

### Resolving what already exists

The interesting case is a name that already exists here. That's a genuine fork in
the road, so the preview asks per agent:

| Choice | What happens |
| --- | --- |
| **Use existing** | The imported step points at the agent you already have, left completely untouched. |
| **Replace** | The existing agent's definition is overwritten **in place**. It keeps its id, so every *other* workflow using it picks the new definition up. The preview warns you how many that is. |
| **Create new** | Both are kept: the file's agent is imported as a separate copy under a disambiguated name (`Reviewer (2)`). |

A workflow whose own name collides offers **Replace** or **Create new** — a
workflow isn't a shared resource the way an agent is.

On first export an object is stamped with a stable **portable id** that travels
in the file, so re-importing a file that originally came from *this* install
recognises the very object it came from and defaults to **Replace**. A bare
**name** match is a weaker signal, so it defaults to **Use existing**: a shared
file must never silently overwrite someone's prompt. That default also governs
scripted imports that send no choices, which are therefore always
non-destructive.

::: warning Replace ripples
Agents are shared. Replacing one edits the definition every live workflow
referencing it will use on its next run. The preview surfaces the count up front,
but it's worth a second look when it's more than one.
:::

A carried **Role** is matched by name and only created when genuinely new, so
importing two workflows that share a persona converges on one role. **Inline step
agents** are never conflict-checked — they belong to their step, so they're
simply recreated with it. A pinned **model** this install doesn't offer is a
warning rather than an error; the agent imports and falls back to the default.

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
agents:
  - export_id: 2f1c…            # stable portable identity
    title: Collector
    task: Gather this week's issues
    use_mcp: true
  - title: Review               # an inline step's private vessel
    task: Check the draft reads well
    inline: true
workflow:
  export_id: db19…
  name: Weekly digest
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
