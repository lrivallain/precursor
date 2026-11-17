---
title: Storage & retention
---

# Storage & retention

Cap how much each feature keeps with automatic sweeps, and free space on demand
from the cleanup cockpit.

## Where the space goes

**Settings → Usage stats** shows the on-disk footprint and a per-table
breakdown, largest first.

| Feature | Table | Bounded by (default) | Set in |
| --- | --- | --- | --- |
| [Agents](/features/agents-mode) timelines | `agent_events` | `agent_event_retention_days` (30) + `agent_event_max_per_session` (2000) | Settings → Agents → Timeline retention |
| Tool results and model [thinking](/features/topics#watching-the-model-think) in [chats](/features/chats) and [topics](/features/topics) | `messages` | `tool_result_retention_days` (0 = keep) | Settings → System |
| [Workflow](/features/workflows) step inputs | `workflow_run_steps` | `workflow_step_retention_days` (30) | Settings → Workflows |
| [Live](/features/live-sessions) transcripts | `meeting_segments` | `live_transcript_retention_days` (7) | Settings → Live |
| [Attachments](/features/attachments) | *(on disk)* | content-addressed blobs + orphan GC | — |

A value of `0` disables that sweep.

## Automatic sweeps

Sweeps run at startup and then daily, when `PRECURSOR_SCHEDULER_ENABLED` is on.
They remove bulk, not meaning:

- **Tool results** — the body is replaced with a short placeholder; the turn
  stays. Older replies also lose their collapsed **Thinking** area.
- **Live transcripts** — raw segments of *ended* sessions are deleted; summary,
  insights and notes stay.
- **Agent timelines** — only the event trace goes; result, artifacts, state and
  posted messages stay. A running agent is never pruned.

### Why agent timelines have two levers

One long agent session can produce tens of thousands of events in an afternoon,
which an age limit wouldn't touch for weeks. So `agent_event_retention_days`
prunes by age and `agent_event_max_per_session` keeps only the newest N events
per agent. Both apply; either can be `0`. They stay editable when Agents mode is
off, since the sweep keeps running.

## The cleanup cockpit

**Settings → Usage stats → Storage cleanup** lists every sweep with what it
would remove **under your current settings** (a dry run), and runs any of them
now. Zero means the window is disabled or nothing has expired.

### Compacting

::: warning Cleaning up is two steps
Deleting rows marks pages **reusable**; it does not shrink the file. Precursor
runs SQLite with `auto_vacuum` off, so the database only gets smaller when you
**Compact** it.
:::

**Compact database** rebuilds the file and reports what it reclaimed. It needs
free disk space about the size of the database while it runs.

### One-off remediations

Two targets clean up data written by older versions. Both are idempotent;
preview, run, then **Compact**.

- **Oversized agent events** — rewrites old event payloads through today's size
  caps. Timeline nodes, tool names and statuses are kept; long blobs are
  shortened and marked as trimmed.
- **Empty agent events** — deletes the streaming-fragment rows older versions
  archived (often most rows on a busy install). Timelines never showed them,
  and they counted against `agent_event_max_per_session`. Safe with a running
  agent.

## Backups are separate

[Backups](/reference/configuration#backup) keep their own snapshots and
retention. Cleaning up never touches existing snapshots, so a snapshot from
before a cleanup still has the old data.
