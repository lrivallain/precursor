---
title: Quick start — Workflows
---

# Quick start: Workflows

[Workflows](/features/workflows) chain agents into a reusable pipeline
(`research → draft → review`) that can run unattended. They are enabled with
[Agents](/guide/quick-start/agents); there is no separate switch.

## Build one

**Workflows → New workflow**, name it, **Edit steps**. The **+** between cards
inserts a step:

| Kind | What it is |
| --- | --- |
| **Agent** | A reusable agent from the Agents section |
| **Inline** | A one-off prompt |
| **Gate** | An automated PASS / FAIL check that can send the run back |
| **Approval** | A human checkpoint; the run waits for you |

**Save steps**, then **Run**. The caret next to **Run** adds a
[run brief](/features/workflows/running#the-run-brief-one-workflow-a-different-subject-each-run),
the subject of this run, so one generic pipeline serves many jobs. Workflows
can also run on a **schedule** or a **webhook**.

## Then try

- A [gate with loop-back](/features/workflows/building#gates-and-loop-back).
- An [approval checkpoint](/features/workflows/building#human-approval-checkpoints)
  before anything irreversible.
- [Fewer tool servers per step](/features/workflows/steps#picking-which-tool-servers-a-step-gets),
  usually the biggest cost saving.
