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
Those draft editors render Markdown, so they carry a **formatting toolbar** and
shortcuts (<kbd>⌘/Ctrl</kbd> + <kbd>B</kbd> / <kbd>I</kbd> / <kbd>K</kbd>, …) that
format the selected text in place.

## Tree organization

Topics form a **self-referencing tree**: nest a topic under a parent to group
related threads.

Over [MCP](/features/mcp), a topic's place in that tree comes back already
resolved as a `path` (`client-a/csu/cto/capacity-review` — the collection slug
followed by the ancestor chain), so an external agent never has to follow
`parent_id` upward to reconstruct it. It mirrors the URL exactly, so a `path`
can be pasted straight into the browser.

Right-click a topic in the left sidebar to rename it, mark it read or unread,
pin or unpin it, set a reminder, open `/notes`, move it to another
[collection](/features/collections), or archive it without first opening the
topic.

Above the tree, [**collections**](/features/collections) split your topics into
separate workspaces of work and filter the sidebar to one at a time — each with
its own optional GitHub repo.

## Addressing a topic

Every topic has two addresses.

The **readable** one describes where the topic sits — its collection, then the
ancestor chain, then its own slug:

```
/topics/client-a/csu/cto/capacity-review
```

It's the URL the address bar shows, and it's meant to be legible. That also
means it *moves*: rename the topic, drag it under a different parent, or send it
to another collection and the readable URL changes with it.

The **permalink** never moves:

```
/t/6f2b1c9e-6b1a-4a0e-9f0f-2f5b0f9f1c2a
```

It's an immutable UUID minted when the topic is created. Opening it resolves the
topic and rewrites the address bar to the readable URL, so you land in the right
place with a legible address. Copy it from **topic settings → Permalink** — it's
the address to paste into an issue, a commit message, or a bookmark that has to
survive reorganising.

::: tip Old links keep working
Links minted before collections joined the URL (`/topics/csu/capacity-review`)
still resolve: the trailing slug is unique on its own, and Precursor rewrites the
address to the current readable form once it has loaded.
:::

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

## When a turn fails

If the provider rejects a turn (an unsupported model, bad credentials) or the
tool loop hits its round cap, the failure is persisted as a **red error notice**
in the transcript — never a green acknowledgement — so a dead turn is never
mistaken for an answer. It survives a reload, so you can come back to it.

The prompt that failed grows a **Retry** button. Retrying replays *that* prompt:
the failed tail (a partial answer, its tool rows, the error notice) is dropped
and the turn runs again against the current model and settings — the prompt keeps
its id and attachments instead of being posted a second time, so the transcript
stays clean. Fix what caused the failure first (switch model in the composer,
add a token in **Settings → Models**) and the retry picks the new setting up.

## Data model

Under the hood a topic is a `Topic` row (a self-referencing tree) with `Message`
children (roles `user` / `assistant` / `system` / `tool`). A topic becomes
"scheduled" simply by having an enabled `TopicSchedule`. See the
[architecture reference](/reference/architecture#database) for the full schema.
