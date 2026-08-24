---
title: Autonomous missions
---

# Autonomous missions

By default an agent runs a **single turn** and comes to rest. Flip **Run
autonomously** on when you start it (off by default) and the task becomes a
**durable objective**: the agent pursues it on its own, continuing between turns
and pulling you in only when it finishes or gets stuck.

- **Objective, not a one-shot prompt.** A **mission strip** at the top of the
  timeline keeps the standing objective in view with a `step N/max` counter.
- **The goal loop.** Each time the agent goes idle, Precursor reads the tail of
  its reply for a control directive and decides what happens next — hand back,
  stop, or keep going. No repost fires between steps, so a multi-step mission
  lands as **one** result in the linked topic/chat at the end.
- **Self-reported progress.** A `PROGRESS:` directive renders a progress bar with
  a percentage and short label on the card and mission strip.
- **Blocked, not silently looping.** If the agent needs a decision it emits
  `NEED_INPUT:` and moves to a **Needs input** state, surfaced as an amber
  callout with an **Answer** button; your reply resumes the mission. It also
  self-blocks if it stalls or spends its step budget, so it can't spin forever.
- **Step budget.** A per-agent step budget (default 12, 1-100) caps how many
  times it may continue before handing back — the primary guardrail.

## The autonomy protocol

An autonomous agent is told to end a reply with a lifecycle directive; the goal
loop parses them (case-insensitive, last match wins).

| Directive | Effect |
| --- | --- |
| `OBJECTIVE_COMPLETE: <summary>` | Mission done — status **Completed**, summary saved, progress forced to 100%, single repost to the linked surface. |
| `NEED_INPUT: <question>` | Agent is blocked — status **Needs input**, the question surfaced for you; your next message unblocks it. |
| `PROGRESS: <0-100> \| <label>` | Optional heartbeat — updates the progress bar and resets the stall counter. |
| `ARTIFACT: <title> \| <content>` | Optional, repeatable — publishes a named output to the shared [blackboard](/features/agents-mode/artifacts-state#shared-artifacts-blackboard). Doesn't change the run's lifecycle. |

All four marker lines are **stripped from the rendered transcript** and
re-surfaced as structured UI, so the prose stays clean. If none appears and the
budget/stall guards allow it, the agent continues to the next step. Plain
(non-autonomous) agents ignore the protocol entirely and rest at **Idle**.

::: warning Experimental
Autonomous missions are new and best kept on a **step budget** with permission
guards in place. Watch a mission's first few runs before trusting it unattended.
:::
