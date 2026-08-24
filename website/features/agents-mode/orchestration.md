---
title: Orchestrating a fleet
---

# Orchestrating a fleet

A single agent is a run; several of them make a **fleet** you watch from one
dashboard. Precursor lets agents **park** until triggered, **budget** and
**retry** themselves, and fire on schedules or external **events** — with a
fleet-wide concurrency cap over the lot.

::: tip Chaining agents into a pipeline
Sequencing one agent after another — research → draft → review — is owned by
**[Workflows](/features/workflows)**, a reusable coordinator that runs a series
of agents in order and passes each step's output to the next. Individual agents
stay independent; a workflow supplies the chaining.
:::

## An agent is a definition; each start is a run

An agent is a **reusable definition** — a title, an objective, a model, a role
and its capability defaults. Every time something starts it, Precursor opens a
separate **run**: its own status, prompt, transcript, artifacts, token meter and
Copilot SDK session.

That split is what makes an agent **safe to share**. Two
[workflows](/features/workflows) can point a step at the same agent and both be
in flight at once — each drives its own run, so neither sees the other's status,
wipes the other's artifacts, or is charged for the other's tokens.

A run records **what triggered it** (`manual`, `workflow`, `schedule`,
`webhook`, `fleet`, or `retry` / `replay`), so the history reads as an audit
trail. Runs are kept, never garbage-collected, and the insights sidebar carries a
**Runs** rail listing the recent ones with the current one highlighted.

The transcript **shows the latest run by default**, and that rail doubles as a
filter: click any run to read that execution alone, or **All runs** to stitch the
history back together. This matters most for a shared agent, where two workflows
driving it at once would otherwise produce one interleaved archive.

## Park an agent until a trigger

Not every agent should run the moment you create it. The composer's **Create
parked (don't run yet)** toggle arms an agent in the **`waiting`** state instead
of starting it — fully configured but idle until its
[webhook](#event-triggers-webhooks) fires, you hit **Start now**, or a
[workflow](/features/workflows) step reaches it. A parked agent is never picked
up by the orphan sweep, so you can pre-stage a run and fire it when ready.

## Budgets & the concurrency governor

- **Concurrency governor.** A fleet-wide cap (`agents_max_concurrent`, default 3)
  limits how many agents run at once; the rest queue and are released as slots
  free up, so a burst of new agents can't stampede the runtime.
- **Token budget (per agent).** Once an agent's cumulative usage crosses its
  budget, it **parks itself in the [inbox](#the-unified-inbox)** for approval
  instead of spending more. Leave it blank for unlimited. The budget is
  **cumulative on the definition**: spend from *every*
  [run](#an-agent-is-a-definition-each-start-is-a-run) counts against it, so
  restarting doesn't hand it a fresh allowance.

## Retry & auto-recovery

Set **max retries** on an agent and a failed run is **automatically retried**
with **exponential backoff** (`agents_retry_backoff_seconds` × 2ⁿ) before it's
finally marked failed, so a transient error recovers itself without you noticing.

## Blueprints (reusable templates)

A **blueprint** saves a task prompt plus a governance profile (model, approval
policy, token budget, retries) as a reusable template. Manage them under
**Settings → Agents → Blueprints**; hit **Run** on one to stamp out a fresh agent
with the same profile — a repeatable job kept one click away instead of retyped.

## Event triggers (webhooks)

An agent can be re-run by an **external event**. Add a **webhook** from the
insights sidebar to mint a unique URL (`POST /api/agents/hooks/{token}`); calling
it re-runs that agent. Bad or disabled tokens 404, so there's no enumeration.

## Scheduling agents

An agent can carry its **own recurrence** so it re-runs its stored task on a
cadence. Each due tick either replays the task on a **fresh transcript**
(`clear_context`) or sends a **follow-up** in the existing conversation. A run is
skipped (not failed) while the agent is mid-turn, waiting on you, archived, or
task-less.

You can also drive an agent from a scheduled topic with slash directives:

- `/agent <uuid> /clear <follow-up>` — reset the transcript and freshen the
  [blackboard](/features/agents-mode/artifacts-state#shared-artifacts-blackboard),
  then send a follow-up.
- `/agent <uuid> /run [extra]` — reset, then replay the agent's own task prompt
  plus an optional one-off extra.

See the [scheduler](/features/scheduler) for how these directives — and `/guard`
gating — fit together.

## The unified inbox

Everything waiting on you — parked **approvals**, raised **Needs input**
questions, and **budget parks** — is collected into one **inbox** strip across
the top of the dashboard, each chip deep-linking to its agent. The dashboard
header also shows a **fleet rollup**: how many agents are running vs queued, and
total tokens consumed against the sum of budgets.
