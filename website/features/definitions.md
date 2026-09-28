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

Open **Settings → Definition files**. A wizard takes you through it:

1. **Overview** — what's declared today, where the files will go (the
   **Agents & workflows** workspace), what stays in the database, and anything
   in the way (a workflow mid-run, two files with the same id).
2. **Review** — every agent and workflow with what happens to its file: new,
   rewritten from the database (and why), or already up to date. Open a line to
   see the **file itself**, as it will be written, next to what's on disk now.
3. **Migrate** — a copy of the database is taken, the files are written and
   **verified** against the database; if anything differs, nothing switches.
4. **Try it out** — the files now declare your agents and workflows. Edit them
   in Files, run workflows, watch the folder check. **Switch back to the
   database** is still one click away.
5. **Clean up** — when you're sure, and only then: the database stops holding a
   copy of your agents and workflows. You confirm by ticking a box and typing
   *clean up*. **After this, switching back isn't possible** (a copy of the
   database is taken first).
6. **Done** — the wizard confirms the migration is finished, with what was
   cleared and where the database copies are.

`precursor validate <folder>` checks a folder from a terminal or CI.

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
