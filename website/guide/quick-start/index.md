---
title: Quick start
---

# Quick start

This walkthrough gets you from a fresh install to your first conversation in a
couple of minutes. If you haven't installed Precursor yet, see the
[installation guide](/guide/installation).

## 1. Start the app

```bash
uv run precursor --dev        # dev stack (hot reload + Vite HMR)
# or, from a published build:
uvx precursor-ai              # single-process, zero setup
```

Open the URL printed in the startup banner. You'll land on the **home** launcher:
a greeting and a grid of section cards (Topics, Chats, Live, Agents, Workflows,
Files, and — when configured — Kanban).

<Screenshot src="/screenshots/home.png" alt="The Precursor home launcher with section cards" />

## 2. Create a topic

Click the **Topics** card, then **New topic**. Give it a name that describes the
thread of work — for example *"Onboarding checklist for new hires"*.

Optionally **link a GitHub issue**: paste an issue URL or number, or create a new
issue from the topic. Once linked, the issue's body, comments, and labels become
live context on every turn — newer comments are preferred over older ones, so the
assistant always reasons over the current state.

<Screenshot src="/screenshots/topics.png" alt="A topic linked to a GitHub issue, with the issue's labels shown as tags" />

## 3. Chat

Type a prompt in the composer and send it. The reply **streams in** with live
markdown rendering — including fenced code blocks and `mermaid` diagrams. Tool
calls are shown inline so you can see what the assistant did.

Useful things to try in the composer:

- **`/`** — open the slash-command picker (skills, memory commands, GitHub
  actions, `/notes`, and more).
- **Attach a file** — drop in an image (used as vision input) or a PDF / DOCX /
  PPTX (text-extracted). See [attachments](/features/attachments).
- **⌘K / Ctrl-K** — open the command palette to jump between sections and
  conversations.

That's the core loop. From here, each of Precursor's other sections has its own
short quick start.

## Start with the other sections

Topics and chats work out of the box. The rest each need a first step of their
own — and in three cases a prerequisite:

| Section | Quick start | Needs first |
| --- | --- | --- |
| 🤖 **Agents** | [Run a task in the background](/guide/quick-start/agents) | the `agents` extra |
| 🎙️ **Live sessions** | [Record and summarise a meeting](/guide/quick-start/live-sessions) | Azure Speech credentials |
| 🔗 **Workflows** | [Chain agents into a pipeline](/guide/quick-start/workflows) | agents enabled |
| 📋 **Kanban** | [Put your issues on a board](/guide/quick-start/kanban) | a global repo + `read:project` |
| 🗂️ **Files** | [Let the assistant edit files](/guide/quick-start/files) | — |

## Where to go next

- [Configuration](/guide/configuration) — connect GitHub and a real model.
- [Topics](/features/topics) — nest them into a tree, put one on a
  [schedule](/features/scheduler), or set a `/reminder`.
- [Chats](/features/chats) — for when you just need a fast answer without the
  ceremony of a topic.
- [Feature guides](/features/) — the full detail behind every section.
