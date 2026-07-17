---
title: Topics
---

# Topics

A **topic** is a long-lived conversation that keeps its own history and context.
It's the heart of Precursor: each topic is an independent thread of work, and it
can be **linked to a GitHub issue** whose body, comments, and labels become live
context on every turn.

<Screenshot src="/screenshots/topics.png" alt="A topic linked to a GitHub issue, showing the issue's labels as tags and a streamed reply" caption="A topic linked to a GitHub issue — the issue's labels tag the chat and its comments feed the assistant's context." />

## Issue-linked context

Link a topic to a GitHub issue (paste a URL/number, or create a new issue from
the topic). From then on, every turn rebuilds the system prompt from:

- the issue **body**,
- its **most-recent comments** (newer comments outweigh older ones), and
- its **labels** (which also show up as tags on the chat).

Because the context is rebuilt on **every** turn, changes to the issue propagate
instantly — the assistant always reasons over the current state of the work. The
result is cached with a TTL (`IssueContextCache`) so repeated turns don't hammer
the GitHub API.

Precursor can also **write back** to GitHub — create or update the issue, and
post comments — from the composer's GitHub actions and the shared draft panel.

## Tree organization

Topics form a **self-referencing tree**: nest a topic under a parent to group
related threads. The sidebar is collapsible and searchable, so a large tree stays
navigable.

## Scheduling & reminders

Any topic can carry a **schedule** so a prompt runs on a cadence — an interval, a
weekday mask, and a daily time-of-day in a timezone. A scheduled prompt that
begins with a slash command is dispatched to that command's backend action; other
prompts run a normal generation turn. See the
[scheduler](/features/scheduler) for cadences, `/guard` probes, and **Run now**.

You can also set a one-shot **reminder** that resurfaces the topic at a specific
time with a posted system message.

## Under the composer

Within a topic's chat you get the full composer toolkit:

- **Streaming** replies over SSE with markdown, mermaid, and code highlighting.
- **`/` slash commands** — [skills](/features/skills-memory), memory commands,
  GitHub create/update/close, `/notes`, and more.
- **[Attachments](/features/attachments)** — images (vision) and documents
  (text-extracted).
- **[MCP tools](/features/mcp)** — enabled tool servers are opened for the turn
  and their tool calls shown inline.
- **Lazy history** — long transcripts load the most recent page and fetch older
  messages as you scroll up.

## Data model

Under the hood a topic is a `Topic` row (a self-referencing tree) with `Message`
children (roles `user` / `assistant` / `system` / `tool`). A topic becomes
"scheduled" simply by having an enabled `TopicSchedule`. See the
[architecture reference](/reference/architecture#database) for the full schema.
