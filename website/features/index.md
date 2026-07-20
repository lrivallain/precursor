---
title: Feature guides
---

# Feature guides

Precursor keeps a small, opinionated core — **topics, chat, GitHub** — and layers
the rest of a follow-up assistant around it. This section is a tour of everything
it can do.

## The sections

Precursor's sidebar is organized into color-coded sections, each a different mode
of working:

| Section | What it's for |
| --- | --- |
| 🧵 **[Topics](/features/topics)** | Long-lived, tree-organized threads, each optionally linked to a GitHub issue used as live context. |
| 💬 **[Chats](/features/chats)** | Quick, throwaway conversations for fast answers. |
| 🎙️ **[Live sessions](/features/live-sessions)** | Record & transcribe a meeting with live insights, Q&A, and a summary. |
| 🤖 **[Agents](/features/agents)** | Autonomous Copilot SDK agents for long-running tasks (opt-in). |
| 🗂️ **[Workspaces & files](/features/workspaces)** | Git clones / local dirs the assistant can browse and edit. |
| 📋 **[Kanban](/features/kanban)** | A board over the GitHub issues linked to your topics. |

**Rearrange them to taste.** Drag any section in the sidebar — whether you use
the vertical icon rail or the horizontal tab switcher — to reorder it. The
arrangement is remembered across reloads and shared between both navigation
styles. When you pick the vertical rail, it also stays on the **home launcher**
so you're never more than a click from any section; **Home** and the ⌘K search
launcher sit together at the top of the rail, above the sections.

## Cross-cutting capabilities

These work across the sections above:

- **[Skills & memory](/features/skills-memory)** — reusable `/slash` prompt
  presets (stored as `SKILL.md` files) and long-term memory injected into every
  conversation.
- **[Scheduler & reminders](/features/scheduler)** — put a topic or agent on a
  cadence, gate runs behind cheap MCP probes with `/guard`, or set one-shot
  reminders.
- **[MCP (both ways)](/features/mcp)** — Precursor is an MCP server *and* an MCP
  client, with built-in tool servers and support for your own.
- **[Command runner](/features/command-runner)** — execute bash / python / node
  inside a throwaway Docker jail.
- **[Attachments](/features/attachments)** — images as vision input; PDF / DOCX /
  PPTX text-extracted; content-addressed on disk, deduped.
- **[Plugins](/features/plugins)** — extend the backend and frontend without
  forking core.

## What's under the hood

The whole thing runs as a **single uvicorn process** in production (FastAPI +
the pre-built React SPA), backed by async SQLAlchemy and Alembic migrations. For
the full picture see the [architecture reference](/reference/architecture) and
[technical stack](/reference/stack).
