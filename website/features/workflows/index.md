---
title: Workflows
---

# Workflows

Chain [agents](/features/agents-mode) into a named, reusable pipeline
(`research → draft → review`) that runs in the background. The workflow owns the
sequencing, hand-off, lifecycle and schedule; the agents stay reusable on their
own. Workflows are on whenever [Agents](/features/agents-mode#enabling-agents)
is enabled.

<Screenshot src="/screenshots/workflows.png" alt="A workflow board showing a five-step pipeline: two task steps, a gate, a human approval checkpoint and an inline publish step, above a completed run header" caption="A pipeline's detail board — the run header, then the step strip carrying all four step kinds." />

| Guide | What it covers |
| --- | --- |
| **[Building a pipeline](/features/workflows/building)** | Creating a workflow, editing steps, the four step kinds. |
| **[Configuring a step](/features/workflows/steps)** | Instructions, placeholders, pipeline state, context and tool scoping. |
| **[Running a pipeline](/features/workflows/running)** | Triggers, run brief, run trace, failures, approvals, cost, notifications. |
| **[Reference](/features/workflows/reference)** | Sharing as YAML, API, MCP tools. |
| **[Authoring spec](/reference/workflow-authoring)** | Exact fields and semantics, for a coding assistant generating a workflow. |

Workflows can also be declared as YAML [definition files](/features/definitions).

## Sidebar and overview

Clicking **Workflows** opens **Overview**: a card gallery with schedules,
[live progress](/features/workflows/running#watching-several-pipelines-at-once)
and run/pause actions. The sidebar holds a searchable list; select a row or card
to open its detail board. **New workflow** opens the builder. Direct
workflow/run links and Back/Forward work as expected.

<Screenshot src="/screenshots/workflows-overview.png" alt="Workflow cards beside a searchable sidebar with an Overview entry" caption="Switch pipelines from the list, or monitor them together from Overview." />

## The four kinds of step

| Kind | Runs | Use it for |
| --- | --- | --- |
| **Agent** | a reusable agent from [Agents](/features/agents-mode) | a stage you want to reuse across pipelines |
| **Inline** | a private, hidden agent that dies with the step | [one-off work](/features/workflows/building#inline-steps-one-off-work-that-isn-t-an-agent) |
| **Gate** | an agent that votes PASS / FAIL | a [quality check](/features/workflows/building#gates-and-loop-back) that can send the run back |
| **Approval** | nobody; the run parks | a [human checkpoint](/features/workflows/building#human-approval-checkpoints) before anything irreversible |

Gates and approvals are **transparent to the data flow**: the step after them
receives the last producing step's output, not the verdict.

## How data flows

- Each step gets the previous producing step's **full answer** plus the
  **artifacts of every earlier step** in this run (labelled, oldest first; only
  the latest attempt of a re-run step). No wiring needed.
- A step can narrow that, or pull specific outputs with placeholders: see
  [what each step is fed](/features/workflows/steps#what-each-step-is-fed).
- Agents never know their neighbours; reordering or reusing steps never touches
  the agents.

## Steps never stop to ask

Runs are unattended, so every task step is told to act on its input directly:
no clarifying questions, no menus, no `NEED_INPUT`. An under-specified detail
gets the most reasonable interpretation. Gates follow their own PASS/FAIL
contract, and an [approval](/features/workflows/building#human-approval-checkpoints)
is the one place a run waits for a person.
