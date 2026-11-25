---
title: Scheduler & reminders
---

# Scheduler & reminders

Run [topics](/features/topics), [agents](/features/agents-mode) and
[workflows](/features/workflows/running#triggers-and-scheduling) on a schedule, and set one-shot
**reminders** on a conversation; the model itself can pause a conversation and
[wake it up later](#self-wakeups). The scheduler runs in-process and needs
`PRECURSOR_SCHEDULER_ENABLED` (on by default).

<Screenshot src="/screenshots/scheduler.png" alt="A topic's settings panel with Run on a schedule enabled, showing the prompt to run each time and two recurrence rules — Monday at 09:00 and Friday at 17:00 — with an Add another schedule button beneath them" caption="The recurrence editor — the same control scheduled topics, agents and workflows share. A schedule can hold several rules and fires at whichever comes first." />

## Recurring topics and agents

Enable **Run on a schedule** in the item's settings panel. Each rule is either
an **interval** or a **time of day** (in a timezone), with a **weekday mask**.

### Several cadences on one item

**Add another schedule** adds a rule, e.g. *every day at 07:00* plus *every
weekday at 12:00*. The item fires at whichever rule comes next. Rules are
independent, but share one prompt, one enable toggle and one next-run time, so
pausing pauses them all. Topics, agents and workflows use the same editor.

::: tip
Prefer several **time-of-day** rules over one short interval when you want runs
at specific moments. Two daily rules cost two runs a day; a 15-minute interval
costs 96.
:::

### Commands vs generation

A scheduled prompt starting with a **slash command** (`/agent run the tests`,
`/gh-sync`, a skill) runs that command, as in the topic composer. Anything else
runs a normal reply.

### Nudging an agent cleanly

A recurring `/agent <uuid> <follow-up>` grows the agent's transcript (and its
token cost) without bound. These reset the transcript first, keeping the uuid:

- `/agent <uuid> /clear <follow-up>` — reset, then send the follow-up.
- `/agent <uuid> /run [extra]` — reset, then replay the agent's own task prompt
  plus an optional extra, so instructions live in one place.

### Approvals while you're away

A scheduled agent that pauses for an approval raises the
["agent needs you" signal](/features/agents-mode/running#the-agent-needs-you-signal)
(notification, 🔔 in the tab title, top of ⌘K). A scheduled topic that needs a
[tool approval](/features/approvals#unattended-runs) notifies you too; if nobody
answers within the schedule's *Approval timeout* (default 60 minutes), the call
is denied and the run finishes.

## `/guard` — gate a run behind a cheap probe

Prefix a scheduled prompt with one or more `/guard` lines to run it only when an
**MCP probe** (one tool call, no LLM) finds something:

```
/guard non-empty workiq fetch {"entityUrls": ["/me/mailFolders/<id>/messages?$select=id&$top=1"]}
/agent <uuid> /run
```

`/guard <predicate> <server> <tool> [json-args]`: `non-empty` runs only when the
tool returns rows, `empty` only when it returns none. Otherwise the run is
skipped silently and rescheduled, so a poller doesn't spend a full turn finding
nothing.

- A malformed or failing guard **fails open**: the run proceeds.
- A server that needs interactive sign-in shows a **re-authenticate** prompt and
  skips until you sign in.
- **Run now** still applies guards, but a skip is reported visibly.

## Reminders

A one-shot **reminder** resurfaces a topic or chat at a set time. Type
**`/reminder`** in its composer to pick a date and time, with an optional note:

```
/reminder ping the vendor about the SLA
```

- One per conversation: a new `/reminder` replaces the old one.
- When it fires: a system message is posted, a browser notification is shown
  (if enabled), and it sits in the sidebar's **Reminders** banner.
- **`/reminder-cancel`** cancels a pending reminder; **`/done`** clears a fired
  one.
- The **Reminder** section of the conversation stats panel shows the time and
  note, with **Edit**, **Done** (once fired) or **Set reminder**. An alarm-clock
  icon on the collapsed rail means one is set.
- The `precursor` [MCP server](/features/mcp) can set, list and cancel
  reminders too.

## Self wakeups

A model waiting on something outside the conversation (a CI run, a deploy, a
long job, a reply) can **pause** the conversation and wake itself up later,
instead of polling or giving up. It schedules a wakeup with a delay (or a time)
and a prompt written for its future self, then ends its turn. When the time
comes, that prompt is sent back into the conversation as a new message,
marked **⏰ Self wake-up**, and the model carries on with the whole history.

- **Where:** topics and chats get the `schedule_wakeup` and `cancel_wakeup`
  tools on every turn, with no MCP server to switch on. [Agents](/features/agents-mode)
  get them from the `precursor` [MCP server](/features/mcp) and are told
  their own id to pass.
- **While paused:** an orange pause icon marks the conversation in the
  sidebar (its tooltip says when it wakes up and what it waits for), and a
  banner above the composer offers **Wake now** and **Cancel**.
- **One per conversation**, between 10 seconds and 30 days away; scheduling
  again replaces it. A woken-up turn can pause again if it still has to wait.
- **Never mid-turn:** if a reply is still generating (or an agent is running
  or waiting on an approval) when the time comes, the wakeup waits for it to
  finish.
- A woken-up turn runs unattended, like a scheduled one: tool calls your
  [approval policy](/features/approvals) holds back are parked for you.
