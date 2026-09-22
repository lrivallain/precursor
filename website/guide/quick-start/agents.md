---
title: Quick start — Agents
---

# Quick start: Agents

[Agents](/features/agents-mode) hand a long-running task to an autonomous agent
that works in the **background** while you do something else.

## Enable them first

The Copilot SDK is a normal dependency. What agents *drive* is a ~90 MB native
runtime, and downloading it stays opt-in. Open **Agents** and click **Install
the Copilot CLI**, then confirm the download. **Settings → Agents** and the
Home launcher's **New agent** surface offer the same setup. Progress and any
errors appear in place while you keep working.

If you already have a `copilot` on your `PATH` (Homebrew, npm, the official
installer), Precursor picks it up and there is nothing to install at all.

Without a saved preference, Agents come on once the runtime resolves. If you
previously turned Agents off, installing the CLI leaves that choice unchanged;
enable it in **Settings → Agents** — the checkbox unlocks as soon as a runtime
is there. A runtime that is installed but failed to start offers restart
guidance, not another download.

Agents stays visible in navigation and the command palette throughout setup
and recovery.

## Run your first agent

Open **Agents → New agent**, describe the task, and start it. You land on the
**dashboard** rather than inside the run: cards are sorted by urgency, so
anything waiting on you floats to the top, and a card shows the tool an agent is
running plus its own one-line narration of what it's doing.

Expect to answer **permission prompts** — until you loosen the
[approval policy](/features/agents-mode/running#approval-policy-per-agent), the agent
asks before each tool call. Set it per agent, or globally in **Settings →
Agents**.

## Then try

- **A [token budget](/features/agents-mode/orchestration#budgets-the-concurrency-governor)** so a
  runaway run parks itself instead of spending.
- **[Run autonomously](/features/agents-mode)** to turn the task into a standing
  objective the agent pursues across several turns.
- **[Chaining agents](/guide/quick-start/workflows)** into a reusable pipeline.

Full detail: [Agents](/features/agents-mode).
