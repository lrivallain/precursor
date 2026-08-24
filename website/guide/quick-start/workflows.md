---
title: Quick start — Workflows
---

# Quick start: Workflows

[Workflows](/features/workflows) chain agents into a reusable pipeline —
`research → draft → review` — that runs unattended.

::: tip Workflows ride on the Agents opt-in
There's no separate switch: [enabling agents](/guide/quick-start/agents) is what
turns Workflows on.
:::

## Build your first pipeline

Open **Workflows → New workflow**, name it, then hit **Edit steps** to author the
pipeline on the board. The **+** between cards inserts a step, and each one is
one of four kinds:

| Kind | What it is |
| --- | --- |
| **Agent** | a reusable agent from the Agents section |
| **Inline** | a one-off prompt that dies with the step |
| **Gate** | an automated PASS / FAIL check that can send the run back |
| **Approval** | a human checkpoint — the run parks until you decide |

Hit **Save steps**, then **Run**.

## Make it reusable

Use the caret next to **Run** to attach a
[run brief](/features/workflows/running#the-run-brief-one-workflow-a-different-subject-each-run)
— the subject for *this* run. That's what lets one generic pipeline ("analyse it
→ review it → report it") serve many jobs, since the brief leads every step's
context rather than just the first.

From there, a workflow can run on a **schedule** or a **webhook**, so the whole
pipeline happens without you.

## Then try

- **A [gate](/features/workflows/building#gates-and-loop-back)** that sends work
  back to an earlier step until it passes.
- **An [approval checkpoint](/features/workflows/building#human-approval-checkpoints)**
  in front of anything irreversible.
- **[Narrowing each step's tools](/features/workflows/steps#picking-which-tool-servers-a-step-gets)** —
  usually the single biggest cost saving in a long pipeline.

Full detail: [Workflows](/features/workflows).
