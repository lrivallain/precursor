---
title: Scheduler & reminders
---

# Scheduler & reminders

Precursor runs an in-process **scheduler** that drives recurring
[topics](/features/topics) **and** scheduled [agents](/features/agents), plus
one-shot **reminders** that resurface a topic at a set time.

## Recurring topics and agents

Any topic or agent runs on a cadence simply by having an **enabled schedule**,
edited from its settings panel. Recurrence supports:

- an **interval**,
- a **weekday mask**, and
- a daily **time-of-day** in a timezone.

A single async ticker enqueues due `TopicSchedule` and `AgentSchedule` rows, and
a bounded worker pool runs each — with DB row **leasing** for crash recovery.

### Commands vs generation

A scheduled prompt that begins with a **slash command** (e.g.
`/agent run the tests`, `/gh-sync`) is dispatched to that command's backend
action — the same commands the chat composer offers on the topic surface, plus
your skills. **Anything else** runs a normal generation turn, the same path as
manual chat.

### Nudging an agent cleanly

A recurring `/agent <uuid> <follow-up>` grows the agent's transcript (and the
input tokens replayed each turn) without bound. Two directives reset the
transcript first while keeping the same uuid so the schedule keeps resolving:

- `/agent <uuid> /clear <follow-up>` — reset, then send the follow-up.
- `/agent <uuid> /run [extra]` — reset, then replay the agent's own task prompt
  (plus an optional one-off extra). This keeps the instructions in **one** place
  (the agent), so the recurring prompt shrinks to a tiny nudge.

## `/guard` — gate a run behind a cheap probe

A scheduled prompt can be prefixed with one or more `/guard` directives that gate
the whole run behind a cheap, deterministic **MCP probe** (no LLM, ~0 tokens):

```
/guard non-empty workiq fetch {"entityUrls": ["/me/mailFolders/<id>/messages?$select=id&$top=1"]}
/agent <uuid> /run
```

`/guard <predicate> <server> <tool> [json-args]` calls a single MCP tool and
classifies its result — `non-empty` runs only when rows come back, `empty` only
when none do. When the predicate isn't satisfied the run is **skipped silently**
and just reschedules. This stops a poller (say, an inbox watcher) from burning a
full ~70K-token turn every tick only to find nothing to do.

Guards are designed to **fail safe**:

- A malformed or failing guard **fails open** — the run proceeds — so a typo or a
  transient MCP error can never silently disable a schedule.
- The one exception is a server that needs interactive sign-in: instead of failing
  open into a turn that would just error, the guard surfaces a **re-authenticate**
  prompt and skips until you sign in.

## Run now

An explicit **Run now** is a *forced* run: the guard still gates (an empty probe
still skips), but the skip is recorded **visibly** — a manual trigger that finds
no work says so, while automatic ticks stay silent to avoid posting every poll.

## Reminders

Set a **one-shot reminder** on a topic or chat and it resurfaces at the chosen
time with a posted system message — a lightweight "come back to this" without a
full recurring schedule.

### The `/reminder` command

Type **`/reminder`** in the composer (on a **topic** or **chat**) to open a
date-and-time picker and schedule the reminder. You can add an optional note that
rides along with it:

```
/reminder ping the vendor about the SLA
```

- **One per conversation** — setting a new `/reminder` **replaces** the existing
  one.
- When the time comes, the conversation resurfaces with a posted system message
  (and a browser notification when those are enabled), and the reminder appears
  in the **Reminders** banner at the top of the sidebar until you deal with it.

Two companion commands manage the lifecycle:

- **`/reminder-cancel`** — cancel the pending reminder on this conversation
  before it fires.
- **`/done`** — mark a **fired** reminder as handled, removing it from the
  Reminders section.

Reminders are also exposed through the built-in `precursor`
[MCP server](/features/mcp), so the model (or another MCP host) can set, list,
and cancel them too.
