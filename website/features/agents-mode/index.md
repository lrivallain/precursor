---
title: Agents
---

# Agents

**Agents mode** hands a long-running task to an autonomous **Copilot SDK** agent
attached to a topic or chat, then lets it run in the **background** while you
monitor the whole fleet from a **control-tower dashboard**. It is **opt-in and
off by default**.

<Screenshot src="/screenshots/agents.png" alt="An agent session timeline showing the task, a reasoning step, and the assistant's answer with suggested replies" caption="An agent session — the task at the top, then a workflow-style timeline: reasoning, tool calls, and the assistant's answer with suggested follow-ups." />

## Start here

| Guide | What it covers |
| --- | --- |
| **[Running an agent](/features/agents-mode/running)** | Following a run, the approval policy, editing safely, and how a background agent reaches you. |
| **[Orchestrating a fleet](/features/agents-mode/orchestration)** | Definitions vs runs, parking, budgets, retries, blueprints, webhooks and schedules. |
| **[Artifacts & state](/features/agents-mode/artifacts-state)** | The shared blackboard agents publish to, and the private scratchpad they remember with. |
| **[Autonomous missions](/features/agents-mode/missions)** | Turning a task into a standing objective, and the directive protocol behind it. |

## Enabling agents

Agents mode isn't part of the default install — it lives behind an `agents`
extra that bundles the native Copilot CLI runtime:

```bash
uv sync --extra agents                 # adds github-copilot-sdk
uv run --extra agents precursor --dev  # …or run the dev stack with it
```

Installing the extra *is* the opt-in: with no stored preference, Agents mode
follows the runtime. **Settings → Agents** is the one control on top of that —
turn it off there to stop the runtime without uninstalling anything. Without the
extra, agents stay off and Settings tells you which install command to run.

::: warning ~90 MB native runtime
The `github-copilot-sdk` wheel bundles the native runtime binary (~90 MB
download, ~145 MB on disk) — which is exactly why it's an opt-in extra rather
than a default dependency. See [installation](/guide/installation#optional-agents-mode).
:::

## The agent dashboard

Agents are meant to run in the **background** — you kick one off and let it work.
So opening Agents mode doesn't drop you into a single run; it lands on a
**control-tower dashboard** for the whole fleet: KPI tiles counting each lane
(**Need you**, **Working**, **Idle / done**, **Scheduled**) above monitor cards
grouped into the same urgency swimlanes.

Each KPI tile doubles as a **filter** — click one to narrow the board to that
lane, click it again to clear — and a **search box** in the header filters by
**agent name** as you type. The two stack, so you can look for a name *within*
"Needs you"; a chip above the lanes names whatever is active and clears both in
one click.

Cards are **urgency-sorted, not chronological**: an agent waiting on you — a
parked approval or a raised **Needs input** question — floats to the top, then
interrupted/failed runs, then live work, then idle. The same ordering is used by
the dashboard and the command palette, so "what needs me next" is always the top
row wherever you look.

While an agent is working, its card shows the **current tool** it is running (and
a `×N parallel` count when several run at once) plus the agent's own **live
narration** — the first plain-language line of the message it is streaming. A
backgrounded agent therefore reads as "what it is doing now" in its own words.

Click any card to drop into that agent's
[timeline](/features/agents-mode/running); hit **New agent** to start a fresh
task. Inside a single agent, **← All agents** returns you to the dashboard, and
per-agent actions (rename, archive, stop, delete) live in that header.

::: tip Chaining agents into a pipeline
Sequencing one agent after another — research → draft → review — is owned by
**[Workflows](/features/workflows)**, a reusable coordinator that runs a series
of agents in order and passes each step's output to the next. Individual agents
stay independent; a workflow supplies the chaining.
:::

## Sharing an agent

An agent's definition — prompt, model, persona, budgets and cadence — can be
**exported to a YAML file** from its settings drawer and imported into another
install. What travels is the definition only: no run history, and no webhook
tokens. See [import & export](/features/transfer).
