---
title: Workflows
---

# Workflows

**Workflows mode** chains independent [agents](/features/agents-mode) into a
reusable, named pipeline that runs in the **background** — `research → draft →
review` — where the *workflow* owns the sequencing, not the agents. Each step is
just an existing agent (or one you create as you build), so agents stay reusable
on their own while the workflow coordinates the hand-off, lifecycle, and schedule.
Like agents, it's **opt-in and off by default**.

::: warning Work in progress
Workflows are a new orchestration layer built on top of agents mode. The linear
`step → step` pipeline is solid, but the surface is still evolving — expect the
UI and controls to keep changing.
:::

<Screenshot src="/screenshots/workflows.png" alt="A workflow board showing a five-step pipeline: two task steps, a gate, a human approval checkpoint and an inline publish step, above a completed run header" caption="A pipeline's detail board — the run header, then the step strip carrying all four step kinds." />

## Start here

| Guide | What it covers |
| --- | --- |
| **[Building a pipeline](/features/workflows/building)** | Creating a workflow, editing steps on the board, and the four step kinds. |
| **[Configuring a step](/features/workflows/steps)** | Instructions, placeholders, pipeline state, and narrowing what a step is fed and may use. |
| **[Running a pipeline](/features/workflows/running)** | Triggers, the run brief, the run trace, failures, cost, and notifications. |
| **[Reference](/features/workflows/reference)** | The `/api/workflows` surface and sharing a workflow as YAML. |

## How it differs from agent dependencies

Agents can already declare `depends-on` links that render a
[workflow strip](/features/agents-mode). Workflows are the **coordinator-owned**
alternative:

- **Reusable, not baked in.** A workflow references agents by id. The same agent
  can appear in several workflows; the chaining lives on the workflow, so you
  reorder or reuse steps without touching the agents themselves.
- **One coordinator.** The workflow drives the run — it starts the first step,
  waits for it to rest, feeds its output to the next, and advances down the line.
  Agents don't trigger each other.
- **Implicit hand-off.** Each step receives the immediately preceding step's
  **full answer** plus its artifacts as a kickoff preamble, mirroring a pipeline
  stage. You don't have to prompt a step to "use the previous result" — the
  coordinator forwards it. No dependency graph to reason about.
- **Shared blackboard.** Beyond that immediate hand-off, a step also inherits the
  **artifacts published by every earlier step** in the run, labelled by step and
  oldest-first. A reviewer three stages down still sees the research inventory the
  first step produced — without the middle steps having to re-forward it. Steps
  that published nothing are skipped, and gates (which leave no deliverable)
  never appear on the board.

## The four kinds of step

Every step is one of four kinds. The first two produce work; the last two judge
it and are **transparent to the data flow** — they forward the last real
producer's output rather than anything of their own.

| Kind | Runs | What it's for |
| --- | --- | --- |
| **Agent** | a reusable agent from the [Agents](/features/agents-mode) section | the normal case — a stage you want to reuse across pipelines |
| **Inline** | a private, hidden vessel that dies with the step | [one-off work](/features/workflows/building#inline-steps-one-off-work-that-isn-t-an-agent) that doesn't deserve a permanent agent |
| **Gate** | an agent that votes PASS / FAIL | an automated [quality check](/features/workflows/building#gates-and-loop-back) that can send the run back to an earlier step |
| **Approval** | nobody — the run parks | a [human checkpoint](/features/workflows/building#human-approval-checkpoints) in front of anything irreversible |

## Steps run autonomously — they never stop to ask

A workflow runs **unattended**: once you start it, there is no human sitting in
the loop to answer a mid-run question. So every task step is launched with a
strict autonomy directive — it must carry out its objective **directly on the
input it was handed**, and it is explicitly forbidden from asking for
clarification, presenting a menu of options, or emitting `NEED_INPUT`. If a
detail is under-specified, the step picks the most reasonable interpretation and
produces the deliverable anyway.

This keeps bare chains flowing without careful prompting. A step like *note this
joke from 0 to 10* just scores the joke it received, rather than stopping to ask
"what did you mean by *note*?" — which would otherwise park the step **Blocked**
and pause the entire run.

Gates are exempt: they follow their own PASS/FAIL contract. And an
[approval checkpoint](/features/workflows/building#human-approval-checkpoints) is
the deliberate exception — the one place a pipeline is *designed* to wait for a
person.
