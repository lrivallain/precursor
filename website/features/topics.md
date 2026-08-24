---
title: Topics
---

# Topics

A **topic** is a long-lived conversation that keeps its own history and context.
Where a [chat](/features/chats) is throwaway, a topic is a thread of work you
return to over days or weeks — and it can be **linked to a GitHub issue** whose
body, comments, and labels become live context on every turn.

<Screenshot src="/screenshots/topics.png" alt="A topic linked to a GitHub issue, showing the issue's labels as tags and a streamed reply" caption="A topic linked to a GitHub issue — the issue's labels tag the chat and its comments feed the assistant's context." />

## Issue-linked context

Link a topic to a GitHub issue — paste a URL or number, or create a new issue
from the topic. Every turn then rebuilds the assistant's context from the issue's
**body**, its **most-recent comments** (newer outweigh older), and its
**labels**, which also tag the chat.

Because that happens on *every* turn, edits to the issue propagate immediately —
the assistant always reasons over the current state of the work.

Precursor writes back too: create or update the issue, and post comments, from
the composer's GitHub actions and the shared draft panel.

## Tree organization

Topics form a tree — nest one under a parent to group related threads.
Right-click any topic in the sidebar to act on it without opening it: rename,
pin, set a reminder, move it to another [collection](/features/collections), or
archive.

Above the tree, [**collections**](/features/collections) split your topics into
separate workspaces and filter the sidebar to one at a time.

## Scheduling & reminders

Any topic can carry a **schedule** so a prompt runs on a cadence, or a one-shot
**reminder** that resurfaces the thread at a set time. See the
[scheduler](/features/scheduler).

## Under the composer

- **Streaming** replies with markdown, mermaid, and code highlighting.
- **`/` slash commands** — [skills](/features/skills-memory), memory, GitHub
  actions, `/notes`.
- **[Attachments](/features/attachments)** — images (vision) and documents.
- **[MCP tools](/features/mcp)** — shown inline as they are called.

## When a turn fails

A failed turn — a rejected model, bad credentials, a tool loop that hits its cap
— is persisted as a **red error notice**, never a green acknowledgement, and it
survives a reload. The prompt that failed grows a **Retry** button that replays
it against the current model and settings, dropping the failed tail. Fix the
cause first (switch model, add a token in **Settings → Models**) and the retry
picks it up.
