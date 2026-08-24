---
title: Feature guides
---

# Feature guides

Precursor is a work follow-up assistant built from several distinct **surfaces** —
long-lived topics, throwaway chats, recorded meetings, autonomous agents and the
pipelines that chain them — sharing one set of tools, personas and memory. This
section is a tour of everything it can do.

## The sections

Precursor's sidebar is organized into color-coded sections, each a different mode
of working:

| Section | What it's for |
| --- | --- |
| 🧵 [**Topics**](/features/topics) | Long-lived, tree-organized threads, each optionally linked to a GitHub issue used as live context. |
| 💬 [**Chats**](/features/chats) | Quick, throwaway conversations for fast answers. |
| 🎙️ [**Live sessions**](/features/live-sessions) | Record & transcribe a meeting with live insights, Q&A, and a summary. |
| 🤖 [**Agents**](/features/agents-mode) | Autonomous Copilot SDK agents for long-running tasks, monitored from a control-tower dashboard (opt-in). |
| 🔗 [**Workflows**](/features/workflows) | Chain those agents into a reusable pipeline the coordinator runs unattended, with gates and approval checkpoints (opt-in). |
| 🗂️ [**Workspaces & files**](/features/workspaces) | Git clones / local dirs the assistant can browse and edit. Shown as **Files** in the sidebar. |
| 📋 [**Kanban**](/features/kanban) | A board over the GitHub issues linked to your topics. |

## Cross-cutting capabilities

These work across the sections above:

- [**Skills, roles & memory**](/features/skills-memory) — reusable `/slash`
  prompt presets (stored as `SKILL.md` files), named assistant personas,
  and long-term memory injected into every conversation.
- [**Scheduler & reminders**](/features/scheduler) — put a topic or agent on a
  cadence, gate runs behind cheap MCP probes with `/guard`, or set one-shot
  reminders.
- [**MCP (both ways)**](/features/mcp) — Precursor is an MCP server *and* an MCP
  client, with built-in tool servers and support for your own.
- [**Command runner**](/features/command-runner) — execute bash / python / node
  inside a throwaway Docker jail or your local machine.
- [**Attachments**](/features/attachments) — images as vision input; PDF / DOCX /
  PPTX and text/code files text-extracted; content-addressed on disk, deduped.
- [**Import & export**](/features/transfer) — share an agent or a workflow as a
  plain YAML file, and choose what happens to anything that already exists on
  import.

## What's under the hood

The whole thing runs as a **single uvicorn process** in production (FastAPI +
the pre-built React SPA), backed by async SQLAlchemy and Alembic migrations.

For the full picture see the [architecture reference](/reference/architecture)
and [technical stack](/reference/stack).
