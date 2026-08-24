---
title: Quick start — Agents
---

# Quick start: Agents

[Agents](/features/agents-mode) hand a long-running task to an autonomous agent
that works in the **background** while you do something else.

## Enable them first

Agents are **opt-in** and ship behind an extra, because the wheel bundles a
~90 MB native runtime:

```bash
uv sync --extra agents                 # adds the Copilot SDK runtime
uv run --extra agents precursor --dev
```

Installing the extra *is* the opt-in — agents come on once the runtime resolves.
**Settings → Agents** is the switch on top of that, and reports whether the
runtime actually started.

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
