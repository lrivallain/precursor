---
title: Introduction
---

# What is Precursor?

Precursor is a **per-topic AI chat interface for work follow-up**, where each
topic maps to a GitHub issue. It's a small, opinionated assistant for tracking
work-in-progress conversations alongside the issues they belong to.

Every chat is scoped to a **topic** that can be linked to (or create) a GitHub
issue. The assistant uses that issue's body, comments, and labels as **live
context** — rebuilt on every turn, so newer updates outweigh older ones.

Under the hood Precursor is deliberately compact: in production it is a **single
`uvicorn` worker** that serves a JSON API and the pre-built React SPA from the
same process. There is **no Node.js runtime in production** and no orchestration
to stand up.

## Why it exists

Most AI chat tools are a flat list of disconnected conversations. Work isn't
flat — it's organized around issues, meetings, and tasks that unfold over days
or weeks. Precursor keeps each thread of work in its own **topic**, hydrated from
the GitHub issue it belongs to, so the assistant always has the current state of
that work at hand.

Around that core it layers the other things a follow-up assistant needs: a place
for throwaway [chats](/features/chats), a [live meeting](/features/live-sessions)
recorder, autonomous [agents](/features/agents) for long-running tasks, a
[scheduler](/features/scheduler) for recurring nudges, and [MCP](/features/mcp)
tools in both directions.

## Highlights

- **Topic-scoped, tree-organized** conversations, each optionally linked to a
  GitHub issue whose labels tag the chat.
- **Streaming chat** over Server-Sent Events with markdown, mermaid, and code
  highlighting.
- **Bring your own model** — GitHub Copilot (default), GitHub Models, Azure AI
  Foundry, or any OpenAI-compatible gateway, with a mock provider for offline
  work.
- **MCP both ways** — Precursor exposes its conversations as an MCP server *and*
  attaches external MCP tool servers per topic.
- **Live meeting assistant** — browser transcription via Azure Speech, live
  insights, Q&A, and an editable summary.
- **Agents mode** (opt-in) — hand a task to an autonomous Copilot SDK agent and
  follow it in a workflow-style timeline.
- **Skills & memory**, a **scheduler** with recurrence and guards, **reminders**,
  **workspaces** the assistant can edit, and a **Kanban** board over your issues.
- **Plugin-ready** — backend entry points plus a frontend extension registry.

## The stack in one line

Python 3.12+ · FastAPI · SQLAlchemy 2 (async) · Alembic · Vite + React 19 +
TypeScript · Tailwind CSS · the `mcp` and `openai` SDKs — with **uv** as the one
toolchain for env, run, build, and release. See the
[technical stack](/reference/stack) for the full breakdown.

## Where to next

<div class="pc-next">

- **[Installation](/guide/installation)** — get the app running locally.
- **[Quick start](/guide/quick-start)** — your first topic in a couple of minutes.
- **[Configuration](/guide/configuration)** — GitHub token, model providers, and
  settings.
- **[Feature guides](/features/)** — a tour of everything Precursor can do.

</div>

::: tip Single-user by design
Precursor ships with **no authentication** and is meant to run bound to
`127.0.0.1`. Don't expose it to a network without your own authenticating
reverse proxy in front of it. See the [security model](/reference/architecture#security-deployment-model).
:::
