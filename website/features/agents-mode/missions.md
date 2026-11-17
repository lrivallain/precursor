---
title: Autonomous missions
---

# Autonomous missions

Turn a task into a standing objective the agent pursues across turns, pulling
you in only when it finishes or gets stuck.

::: warning Experimental
Keep missions on a **step budget** with permission guards, and watch the first
few runs before leaving one unattended.
:::

Tick **Run autonomously** when starting an agent (off by default; without it an
agent runs a single turn and rests at **Idle**).

- **Mission strip.** The timeline header shows the objective and a
  `step N/max` counter.
- **Goal loop.** Each time the agent goes idle, Precursor reads the end of its
  reply for a directive (below) and completes, blocks, or continues. Nothing is
  reposted between steps: the linked topic/chat gets one result at the end.
- **Progress.** `PROGRESS:` drives a progress bar on the card and the strip.
- **Needs input.** `NEED_INPUT:` shows an amber callout with **Answer**; your
  reply resumes the mission. The agent also blocks itself when it stalls or
  exhausts its step budget.
- **Step budget.** Default `12`, range 1–100: the main guardrail.

## The autonomy protocol

The agent ends a reply with one of these lines (case-insensitive; the last match
wins):

| Directive | Effect |
| --- | --- |
| `OBJECTIVE_COMPLETE: <summary>` | Status **Completed**, summary saved, progress 100%, one repost to the linked surface. |
| `NEED_INPUT: <question>` | Status **Needs input**; your next message unblocks it. |
| `PROGRESS: <0-100> \| <label>` | Updates the progress bar and resets the stall counter. |
| `ARTIFACT: <title> \| <content>` (or the [block form](/features/agents-mode/artifacts-state#shared-artifacts-blackboard)) | Publishes a named output. Repeatable; doesn't change the lifecycle. |

Directive lines are stripped from the transcript and shown as UI instead. With
no directive, the agent continues to the next step if the budget and stall
guards allow.
