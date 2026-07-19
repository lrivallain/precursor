---
title: Quick start
---

# Quick start

This walkthrough gets you from a fresh install to your first topic-scoped
conversation in a couple of minutes. If you haven't installed Precursor yet, see
the [installation guide](/guide/installation).

## 1. Start the app

```bash
uv run precursor --dev        # dev stack (hot reload + Vite HMR)
# or, from a published build:
uvx precursor-ai              # single-process, zero setup
```

Open the URL printed in the startup banner. You'll land on the **home** launcher:
a greeting and a grid of section cards (Topics, Chats, Live, Agents, Files, and —
when enabled — Kanban).

<Screenshot src="/screenshots/home.png" alt="The Precursor home launcher with section cards" />

::: tip No credentials? No problem.
With no model credentials configured, Precursor automatically falls back to a
**mock provider** that streams a deterministic reply — so the whole flow stays
usable offline while you explore. Wire up a real model when you're ready in
[Configuration](/guide/configuration).
:::

## 2. Create a topic

Click the **Topics** card, then **New topic**. Give it a name that describes the
thread of work — for example *"Onboarding checklist for new hires"*.

Optionally **link a GitHub issue**: paste an issue URL or number, or create a new
issue from the topic. Once linked, the issue's body, comments, and labels become
live context on every turn — newer comments are preferred over older ones, so the
assistant always reasons over the current state.

<Screenshot src="/screenshots/topics.png" alt="A topic linked to a GitHub issue, with the issue's labels shown as tags" />

## 3. Chat

Type a prompt in the composer and send it. The reply **streams in** over
Server-Sent Events with live markdown rendering — including fenced code blocks
and `mermaid` diagrams. Tool calls (if any MCP servers are enabled for the turn)
are shown inline so you can see what the assistant did.

Useful things to try in the composer:

- **`/`** — open the slash-command picker (skills, memory commands, GitHub
  actions, `/notes`, and more).
- **Attach a file** — drop in an image (used as vision input) or a PDF / DOCX /
  PPTX (text-extracted). See [attachments](/features/attachments).
- **⌘K / Ctrl-K** — open the command palette to jump between sections and
  conversations.

## 4. Organize as you go

- **Nest topics** into a tree to group related threads.
- **Put a topic on a schedule** so a prompt runs on a cadence — see the
  [scheduler](/features/scheduler).
- **Set a reminder** to resurface a topic at a specific time.
- Spin up a quick **[chat](/features/chats)** when you just need a fast answer
  without the ceremony of a topic.

## Where to go next

- [Configuration](/guide/configuration) — connect GitHub and a real model.
- [Feature guides](/features/) — the full tour: live meetings, agents,
  workspaces, MCP, and more.
