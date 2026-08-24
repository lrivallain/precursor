---
title: Introduction
---

# What is Precursor?

> **Opinionated approach to work follow-up, built as an AI assistant.**

Precursor is an AI assistant for **following up on work** — the threads, meetings
and tasks that unfold over days or weeks rather than in a single sitting.

It's built from several distinct **surfaces**, each suited to a different mode of
working: long-lived [topics](/features/topics) that can hydrate themselves from a
GitHub issue, throwaway [chats](/features/chats) for a fast answer, recorded
[live meetings](/features/live-sessions), autonomous
[agents](/features/agents-mode) for long-running tasks, and
[workflows](/features/workflows) that chain those agents into a reusable
pipeline. They share one set of [tools](/features/mcp),
[personas and memory](/features/skills-memory), so what you teach it in one place
applies everywhere.

Under the hood Precursor is deliberately compact: normal runtime is a **single
`uvicorn` worker** that serves a JSON API and the pre-built React SPA from the
same process.

## Why it exists

Most AI chat tools are a flat list of disconnected conversations. Work isn't
flat — it's organized around issues, meetings, and tasks with their own lifespan.
So instead of one undifferentiated stream, Precursor gives each kind of work a
surface that fits it, and keeps the context attached to the work rather than to
the conversation you happened to have about it.

A [topic](/features/topics) linked to a GitHub issue rebuilds its context from
that issue on **every turn**, so newer comments outweigh older ones and the
assistant always reasons over the current state. An
[agent](/features/agents-mode) keeps a durable scratchpad across runs. A
[workflow](/features/workflows) remembers what it already processed. In each case
the work carries its own memory.

## Highlights

- **Topic-scoped, tree-organized** conversations, each optionally linked to a
  GitHub issue whose labels tag the chat — alongside quick **chats** for
  throwaway questions.
- **Streaming chat** over Server-Sent Events (sse) with markdown, mermaid, and
  code highlighting.
- **Bring your own model** — GitHub Copilot (default), Azure AI Foundry, or any
  OpenAI-compatible gateway.
- **MCP both ways** — Precursor exposes its conversations and features as an MCP
  server *and* attaches external MCP tool servers to extend its capabilities.
- **Live meeting assistant** — Meeting transcription via Azure Speech, live
  insights, Q&A, and an editable summary.
- **Agents mode** (opt-in) — hand a complex task to an autonomous Copilot SDK agent and follow it in a workflow-style timeline.
- **Workflows** (opt-in) — chain those agents into a reusable, named pipeline
  (`research → draft → review`) that runs unattended on a schedule or a webhook,
  with quality gates and human approval checkpoints.
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
