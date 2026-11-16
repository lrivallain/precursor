---
title: Definition files
---

# Definition files

Declare agents, workflows, roles and live-session
[summary templates](#summary-templates) in **YAML files** you can keep in git and review like
code. The database keeps only runtime state: status, progress, run history, schedules,
triggers.

::: warning Work in progress
Still evolving. A new install starts in files mode. An install that already has agents,
workflows or custom roles keeps running from the database until you migrate it. Full design and
format: [`docs/definitions.md`](https://github.com/lrivallain/precursor/blob/main/docs/definitions.md).
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
<code v-pre>{{step.triage.output}}</code>) survive a reorder. Validation is strict (unknown or
duplicate keys and contradictions are errors), and JSON Schemas give editors completion.
`precursor validate <folder>` checks a folder from a terminal or CI.

## Roles as files

[Assistant roles](/features/skills-memory#assistant-roles) are `roles/<name>.role.yaml` files
with a `name` and a `prompt`.

- Everything refers to a role by **name** (case-insensitive), so roles can't be renamed in the
  app. An undefined name falls back to `default`.
- `roles/default.role.yaml` is re-created when missing.
- Deleting a role in Settings also clears it from agent and workflow files. Uncheckable,
  conflicting or unwritable files block the deletion instead.

## Getting there from today's data

While the database still declares agents and workflows, their home pages offer **Start the
migration** (**Not now** hides it for a week). Or open **Settings → Definition files**. The
wizard:

1. **Overview**: what's declared, where files go (the **Definitions** workspace), and blockers
   (a workflow mid-run, duplicate ids).
2. **Review**: each agent, workflow and role, with the file to be written next to what's on
   disk.
3. **Migrate**: backs up the database, writes the files and **verifies** them; any difference
   aborts the switch.
4. **Try it out**: files are now the source. **Switch back to the database** is one click
   away.
5. **Clean up** (optional, irreversible): the database stops holding a copy. Confirm by
   ticking a box and typing *clean up*. A database copy is taken first.
6. **Done**: shows what was cleared and where the backups are.

## Summary templates

A `summaries/*.summary.yaml` file is a template for a
[live session recap](/features/live-sessions#your-own-templates). Templates are always read
from the folder (no migration needed) and edits apply to the next recap.

```yaml
# summaries/customer-call.summary.yaml
kind: summary
id: customer-call            # remembered as the last template used
name: Customer call notes    # shown in the picker
description: Needs, commitments on each side, and next steps.
prompt: |
  You are an account manager writing up a call with a customer…
```

The `prompt` sets sections, tone and length; transcript, notes, insights, linked context and
language are passed for you. A file with a built-in's **id** overrides it (delete the file to
restore it). A file with errors is skipped with the reason shown; the built-in stays
available.

## Working in files mode

- **Edit either side.** Saving in the app writes the file; file edits show in the app within a
  second. Comments are lost when the app rewrites a file.
- **New files appear automatically**; moving a file keeps its history.
- **A broken file never runs the old copy**: the run is refused with the reason.
- **Permission changes need you.** A file that widens an agent's permissions (approval policy,
  autonomy, tools, MCP servers, budget, new steps) can't run until you **Review & accept** it.
  The assistant's own file tools can't write definition files.
- **Git**: set `PRECURSOR_DEFINITIONS_WORKSPACE` to make a [workspace](/features/workspaces)
  the definitions folder, then pull, edit and push from Files, with completion and inline
  validation (see [the Definitions workspace](/features/workspaces#the-definitions-workspace)).

See also [Import & export](/features/transfer) and the
[configuration reference](/reference/configuration).
