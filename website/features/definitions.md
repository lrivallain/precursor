---
title: Definition files
---

# Definition files

Declare agents and workflows in **YAML files** — one per agent, one per
workflow — and let the database keep only what happens when they run: status,
progress, run history, schedules, triggers. Files can live in git, be reviewed
like code, and move between machines.

::: warning Work in progress
Opt-in and still evolving. By default Precursor keeps running everything from
the database, as it always has. The full design, file format and roadmap live
in [`docs/definitions.md`](https://github.com/lrivallain/precursor/blob/main/docs/definitions.md).
:::

## The files

```yaml
# agents/inbox-triager.agent.yaml
kind: agent
id: inbox-triager          # stable identity: never change it
title: Inbox triager
prompt: Sort every thread into act now / this week / later.
approval_policy: balanced
capabilities:
  mcp_servers: [workiq]
```

```yaml
# workflows/morning-briefing.workflow.yaml
kind: workflow
id: morning-briefing
name: Morning briefing
steps:
  - key: triage
    agent: agents/inbox-triager.agent.yaml
  - key: check
    kind: gate
    prompt: PASS if every point is backed by a thread.
    on_fail: triage
```

Steps have **keys**, so references (`on_fail`, context sources,
`{{step.triage.output}}`) survive a reorder. Files are checked strictly —
unknown keys, duplicate keys and contradictions are errors — and JSON Schemas
give editors completion.

## Getting there from today's data

1. **Check a folder** with `precursor validate <folder>` (works in CI), or
   `GET /api/definitions/check` for the configured folder
   (`PRECURSOR_DEFINITIONS_DIR`, by default `<data dir>/definitions`).
2. **Export** your existing agents and workflows with
   `POST /api/definitions/export`. Nothing is deleted; each row is linked to its
   file.
3. **Switch on files mode** with `PRECURSOR_DEFINITIONS_SOURCE=files`. Linked
   agents and workflows are then declared by their files everywhere — runs, the
   app, search — and each run records which file version it used. Pages show
   which file declares them.

## Working in files mode

- **Edit either side.** Saving in the app writes the file; editing the file
  shows up in the app within a second. (Comments in a file aren't kept when the
  app rewrites it.)
- **New files appear on their own**, and moving a file keeps its history.
- **A broken file never runs the old copy**: the run is refused with the reason.
- **Permission changes need you.** A file that widens what an agent may do —
  approval policy, autonomy, tools, MCP servers, budget, new steps — can't run
  until you choose **Review & accept** on its page. The assistant's own file
  tools can't write definition files at all.
- **Keep them in git** with `PRECURSOR_DEFINITIONS_WORKSPACE`: a
  [workspace](/features/workspaces) becomes the definitions folder, so you pull,
  edit (with the check shown under the editor) and push from the Files section.

See also [Import & export](/features/transfer) for sharing a single agent or
workflow as a file, and the [configuration reference](/reference/configuration).
