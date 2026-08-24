---
title: Storage & retention
---

# Storage & retention

Precursor keeps everything it has ever done — conversations, tool results,
meeting transcripts, agent timelines. That's the point, but left unbounded a busy
install grows without limit. **Storage & retention** is the answer: automatic
sweeps that cap what each feature keeps, plus an on-demand cockpit for when you
want space back *now*.

## Where the space goes

**Settings → Usage stats** reports the on-disk footprint and a per-table
breakdown, largest first. That table is the starting point for any cleanup —
in practice one or two tables dominate everything else.

The usual suspects, and what bounds each:

| Feature | Table | Bounded by |
| --- | --- | --- |
| [Agents](/features/agents-mode) timelines | `agent_events` | `agent_event_retention_days` + `agent_event_max_per_session` |
| Tool results in [chats](/features/chats) and [topics](/features/topics) | `messages` | `tool_result_retention_days` |
| [Live](/features/live-sessions) transcripts | `meeting_segments` | `live_transcript_retention_days` |
| [Attachments](/features/attachments) | *(on disk, not in the DB)* | content-addressed blobs + orphan GC |

## Automatic sweeps

Every sweep runs once on startup and then daily in the background. They are
gated by `PRECURSOR_SCHEDULER_ENABLED`, and a sweep whose window is `0` is a
cheap no-op — so turning retention off costs nothing.

Each one is deliberately **conservative about meaning and aggressive about
bytes**:

- **Tool results** — the row and its `tool_call_id` metadata stay, only the body
  is replaced with a short placeholder. The conversation never loses a turn.
- **Live transcripts** — only raw segments of *ended* sessions are deleted.
  Summary, insights and notes survive, so a cleaned session still shows its
  recap. An active recording is never touched.
- **Agent timelines** — the agent keeps its result, artifacts, state and posted
  messages; only the verbose event trace goes. A **running** agent is never
  pruned, because its live timeline is rebuilt from those rows after a restart.

### Why agent timelines have two levers

Agent traffic is **bursty rather than aged**. A single long autonomous session
can archive tens of thousands of events in an afternoon — a time window wouldn't
touch any of it for weeks. So `agent_event_retention_days` prunes by age, and
`agent_event_max_per_session` caps how many events any one agent may keep,
newest first. Either can be disabled with `0`; they compose.

Both live under **Settings → Agents → Timeline retention**, next to the feature
they govern — the same way Live owns its transcript window. They stay readable
when Agents mode is off, because the archived events outlive the toggle and the
sweep keeps running.

## The cleanup cockpit

Retention on a daily cadence is the right default, but a poor answer to "my
database is 126 MB *right now*". **Settings → Usage stats → Storage cleanup**
lists every sweep with what it would remove **under your current settings**, then
lets you run it immediately.

Nothing is deleted to produce those figures — the preview is a dry run. A target
reading zero means its window is disabled or nothing has expired yet.

### Compacting

::: warning Cleaning up is two steps
Deleting rows marks pages **reusable**; it does not shrink the file. Precursor
runs SQLite with `auto_vacuum` off, so the database only gets smaller when you
**Compact** it.
:::

**Compact database** rebuilds the file so freed pages return to the filesystem,
and reports how much it reclaimed. Run it after a cleanup — that's the step you
actually see on disk. It briefly needs free space roughly equal to the database
size while it rebuilds.

### Oversized agent events

One target is a **remediation**, not a retention window. Archived event payloads
written before the current size caps existed keep their original size forever:
retention can't reach them, because an oversized payload isn't necessarily old
and counts as one row against a per-session ceiling however many KB it holds.

**Oversized agent events** rewrites those payloads through today's caps. Every
timeline node, tool name and status is preserved — only long captured blobs are
shortened, and each trimmed value is marked so it can't be mistaken for the
original. It's idempotent, so running it twice is harmless.

## Backups are separate

The [backup](/reference/configuration#backups) job copies the database and blobs
into a plain folder on its own schedule and keeps its own snapshot retention.
Cleaning up doesn't touch existing snapshots — but a snapshot taken *before* a
cleanup still holds the old data, which is exactly what you want if you ever need
to go back.
