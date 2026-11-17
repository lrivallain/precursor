---
title: Orchestrating a fleet
---

# Orchestrating a fleet

Park agents until triggered, cap their spend, retry failures, and fire them on
schedules or webhooks, under one fleet-wide concurrency cap. To chain agents,
use [Workflows](/features/workflows).

## An agent is a definition; each start is a run

An agent is a reusable **definition** (title, objective, model, role,
capabilities). Every start opens a separate **run** with its own status,
transcript, artifacts, token meter and SDK session. So two
[workflows](/features/workflows) can use the same agent concurrently without
seeing each other's status, artifacts or tokens.

Each run records its trigger: `manual`, `workflow`, `schedule`, `webhook`,
`fleet` (released by the governor), `retry` or `replay`. Runs are kept. The
insights sidebar's **Runs** rail lists recent runs; the transcript shows the
latest by default; click a run to read it alone, or **All runs** for the whole
history.

## Park an agent until a trigger

Tick **Create parked (don't run yet)** in the composer to create the agent in
the **`waiting`** state. It stays idle until its
[webhook](#event-triggers-webhooks) fires, you click **Start now**, or a
[workflow](/features/workflows) step reaches it. Parked agents are never picked
up by the orphan sweep.

## Budgets & the concurrency governor

| Control | Where | Behaviour |
| --- | --- | --- |
| Concurrency cap | `agents_max_concurrent` (default `3`; `0` = no cap) | Extra agents queue and start as slots free up. |
| Token budget | per agent (blank = unlimited) | When cumulative usage across **all** of the agent's runs crosses it, the agent parks in the [inbox](#the-unified-inbox) for approval. Restarting doesn't reset it. |

## Retry & auto-recovery

Set **max retries** on an agent (0–10) and a failed run is retried
automatically with exponential backoff: `agents_retry_backoff_seconds`
(default `60`) × 2<sup>n−1</sup> for attempt *n*. It is marked failed once
retries run out.

## Blueprints (reusable templates)

A **blueprint** stores a task prompt plus a governance profile (model, approval
policy, token budget, retries). Manage them in **Settings → Agents →
Blueprints**; **Run** creates a new agent from one.

## Event triggers (webhooks)

Add a **webhook** from the insights sidebar to get a URL,
`POST /api/agents/hooks/{token}`. Calling it re-runs the agent. Unknown or
disabled tokens return 404.

## Scheduling agents

An agent can carry its own recurrence. Each tick either replays the task on a
**fresh transcript** (`clear_context`) or sends a **follow-up** in the existing
conversation. A tick is skipped (not failed) while the agent is mid-turn,
waiting on you, archived, or has no task.

From a scheduled topic you can also drive an agent with directives:

| Directive | Effect |
| --- | --- |
| `/agent <uuid> /clear <follow-up>` | Reset the transcript and [blackboard](/features/agents-mode/artifacts-state#shared-artifacts-blackboard), then send the follow-up |
| `/agent <uuid> /run [extra]` | Reset, then replay the agent's task prompt plus an optional extra |

See the [scheduler](/features/scheduler) for combining these with `/guard`.

## The unified inbox

Parked **approvals**, **Needs input** questions and **budget parks** appear as
chips across the top of the dashboard, each linking to its agent. The header
also shows a fleet rollup: running vs queued, and tokens used against the sum
of budgets.
