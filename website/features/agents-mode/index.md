---
title: Agents
---

# Agents

Hand a long-running task to an autonomous **Copilot SDK** agent, let it work in
the background, and watch the whole fleet from one dashboard.

<Screenshot src="/screenshots/agents-overview.png" alt="The agent fleet overview with monitoring cards beside a searchable agent sidebar" caption="The sidebar is for switching agents; Overview keeps the whole fleet in view." />

| Guide | What it covers |
| --- | --- |
| **[Running an agent](/features/agents-mode/running)** | The timeline, thinking, approvals, tool scoping, editing, notifications. |
| **[Orchestrating a fleet](/features/agents-mode/orchestration)** | Runs vs definitions, parking, budgets, retries, blueprints, webhooks, schedules. |
| **[Artifacts & state](/features/agents-mode/artifacts-state)** | Published results (the blackboard) and the private per-agent scratchpad. |
| **[Autonomous missions](/features/agents-mode/missions)** | Standing objectives and the directive protocol. |

To chain agents (research → draft → review), use **[Workflows](/features/workflows)**:
agents stay independent and the workflow owns the sequencing. Agents can also be
declared as YAML [definition files](/features/definitions).

## Enabling agents

Agents needs the native **Copilot CLI**. The SDK is a normal dependency; the
CLI is a separate, explicit download.

1. Open **Agents** (or **Settings → Agents**, or **New agent** on Home).
2. Click **Install the Copilot CLI** and confirm. You can leave and come back to
   the progress.

<Screenshot src="/screenshots/agents-setup.png" alt="Agents setup with an explicit Install the Copilot CLI action" caption="Install directly from Agents, without hiding the section or leaving the app." />

How the on/off switch behaves:

- With no saved preference, Agents turns on as soon as a CLI resolves.
- If you explicitly turned it **off**, installing doesn't turn it back on.
- **Enable Agents mode** can't be ticked until a runtime resolves. A saved *on*
  is never revoked by an outage; it works again once the runtime returns.
- If the runtime is installed but failed to start, setup offers a restart
  (confirmed separately) instead of another download.
- Turning Agents off hides its settings and blueprints without clearing them.
  [Timeline retention](/features/storage) stays visible while archived events
  exist, because its sweep keeps running.

::: warning ~90 MB native runtime
The CLI is ~90 MB (~145 MB on disk), so Precursor never downloads it on its own.
A system-wide `copilot` (Homebrew, npm, official installer) works as-is.
:::

### Pointing at a specific CLI

Precursor resolves the runtime read-only, taking the first of:

1. `COPILOT_CLI_PATH`, if it points at an existing file.
2. The SDK download cache (`~/Library/Caches/github-copilot-sdk` on macOS,
   `~/.cache/github-copilot-sdk` on Linux).
3. A `copilot` executable on `PATH`.

**Settings → Agents** shows which binary is in use. If none resolves, install the
[Copilot CLI](https://github.com/github/copilot-cli) or set `COPILOT_CLI_PATH`.

## The dashboard

Clicking **Agents** always opens **Overview**: KPI tiles for each lane
(**Need you**, **Working**, **Idle / done**, **Scheduled**), the
[unified inbox](/features/agents-mode/orchestration#the-unified-inbox), and
monitor cards in the same lanes. A searchable agent list sits in the sidebar
(resizable and collapsible; in the navigation drawer on phones).

- **Urgency order, not chronological.** Waiting on you first (approval or
  **Needs input**), then interrupted/failed, then working, then idle. The
  sidebar and the ⌘K palette use the same order.
- **Filters.** Click a KPI tile to filter to that lane; type in the search box
  to filter by name. A chip shows active filters; **Clear** resets them.
- **Standalone vs workflow agents.** An agent used by any non-archived workflow
  is listed under **Workflow agents** (once, however many workflows use it).
  Untick **Show workflow agents** to hide them; it narrows the views only.
  **Fleet totals** (tokens, concurrency, completed/failed) stay fleet-wide.
  Inline workflow steps never appear here.
- **Live cards.** A working agent's card shows the current tool (`×N parallel`
  when several run), the first line of what it is writing, or a pulsing
  *Thinking…* line while the model
  [thinks](/features/agents-mode/running#watching-an-agent-think).

Click a card or a row to open the agent's
[timeline](/features/agents-mode/running); **← All agents** returns to the
dashboard. Rename, run, archive, stop and delete live in the agent header.
**New agent** opens the task composer.

## Sharing an agent

Export an agent's definition (prompt, model, persona, budgets, cadence) to YAML
from its settings drawer and import it elsewhere. Run history and webhook
tokens don't travel. See [import & export](/features/transfer).
