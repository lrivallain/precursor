---
title: Quick start — Agents
---

# Quick start: Agents

[Agents](/features/agents-mode) run a long task autonomously in the background.

## Enable them

Agents drive the Copilot CLI runtime (~90 MB, opt-in download). Open **Agents**
and click **Install the Copilot CLI** (also offered in **Settings → Agents** and
the Home launcher's **New agent**). A `copilot` already on your `PATH` is used
as is.

Agents switch on once the runtime resolves, unless you turned them off before:
then enable them in **Settings → Agents**.

## Run one

**Agents → New agent**, describe the task, start it. The **dashboard** sorts
cards by urgency (anything waiting on you first) and shows each agent's current
tool and a one-line status.

By default the agent asks before each tool call. Loosen the
[approval policy](/features/agents-mode/running#approval-policy-per-agent) per
agent or in **Settings → Agents**.

## Then try

- A [token budget](/features/agents-mode/orchestration#budgets-the-concurrency-governor)
  so a runaway run parks itself.
- [Autonomous runs](/features/agents-mode) that pursue an objective across turns.
- [Workflows](/guide/quick-start/workflows) to chain agents.
