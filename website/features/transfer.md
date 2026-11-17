---
title: Import & export
---

# Import & export

Export an agent or workflow to a YAML file you can diff, commit or share, and import one back
with a preview of what would change.

<Screenshot src="/screenshots/transfer.png" alt="The Workflows gallery with two pipeline cards and an Import button in the header" caption="Import sits beside New workflow on the gallery; every card can be exported from its detail board." />

## Exporting

- **Workflow**: the **download** icon in its detail board header. The file includes the agents
  its steps use, as dependencies.
- **Agent**: **Export as YAML** in its settings drawer. Step-private agents can only travel
  with their workflow.

Exported: prompt and objective, model, autonomy and step budget, approval policy, token budget
and retries, capability toggles, [role](/features/skills-memory#assistant-roles), and for a
workflow the full step wiring (gate loop-backs, approval checkpoints, per-step instructions).
Schedules travel with all their [rules](/features/scheduler#several-cadences-on-one-item) but
arrive **paused**.

Not exported:

| Left out | Why |
| --- | --- |
| Status, run history, artifacts, token counters | Runtime state, not definition. |
| Copilot SDK session handle | Only meaningful on this install. |
| **Webhook tokens** | Per-install credentials. Re-mint on the target. |
| Topic / chat links | Machine-local. Imported agents arrive unattached. |

## Importing

**Import** sits next to **New workflow** (Workflows gallery) and **New agent** (Agents
dashboard). Drop a `.yaml` file; Precursor previews the result before writing anything.

When an agent with the same identity already exists, choose per agent:

| Choice | Effect |
| --- | --- |
| **Use existing** | The step points at your existing agent, untouched. |
| **Replace** | Overwrite the existing agent in place (same id): every workflow using it picks up the change. The preview shows how many. |
| **Create new** | Import a separate copy (`Reviewer (2)`). |

A colliding workflow offers **Replace** or **Create new**.

Defaults: a matching **portable id** (stamped on first export, so it's the same object
re-imported) defaults to **Replace**; a bare **name** match defaults to **Use existing**.
Scripted imports that send no choices use these defaults, so they never overwrite on a name
match.

::: warning Replace ripples
Replacing a shared agent changes it for every workflow that uses it on their next run.
:::

- Roles are matched by name and only created when new.
- Inline step agents are recreated with their step, never conflict-checked.
- A pinned model this install doesn't offer is a warning; the agent falls back to the default.

## API

| Method & path | Purpose |
| --- | --- |
| `GET /api/transfer/workflows/{id}` | Download a workflow (+ its agents) as YAML |
| `GET /api/transfer/agents/{id}` | Download an agent as YAML |
| `POST /api/transfer/preview` | `{ "content": "<yaml>" }` → conflicts + warnings; writes nothing |
| `POST /api/transfer/import` | `{ "content": "<yaml>", "resolutions": [...] }` → applies it |

A resolution is `{ "kind": "agent" | "workflow", "index": <n> | null, "action": "replace" |
"create" | "link" }`, keyed as the preview reported the conflict. Unresolved conflicts use the
safe default.

## File format

```yaml
format: 1
kind: workflow
exported_by: Precursor 2026.1
agents:
  - export_id: 2f1c…            # stable portable identity
    title: Collector
    task: Gather this week's issues
    use_mcp: true
  - title: Review               # an inline step's private agent
    task: Check the draft reads well
    inline: true
workflow:
  export_id: db19…
  name: Weekly digest
  max_loops: 3
  steps:
    - agent: 0                  # index into `agents`, never a database id
      name: Collect
      kind: task
    - agent: 1
      kind: gate
      on_fail_position: 0
    - name: Publish?
      kind: approval
      on_reject: stop
```

Steps reference agents by index in the file. A `format` newer than the running version is
refused rather than partially read.

To keep every agent and workflow as files permanently, see
[Definition files](/features/definitions) (work in progress).
