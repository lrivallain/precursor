---
layout: home

hero:
  name: Precursor
  text: Work follow-up, one topic at a time
  tagline: >-
    A single, local-first app that keeps your AI conversations next to the GitHub
    issues they belong to — with live meetings, autonomous agents, MCP tools, and
    a scheduler, all in one uvicorn process.
  actions:
    - theme: brand
      text: Get started
      link: /guide/introduction
    - theme: alt
      text: Explore features
      link: /features/
    - theme: alt
      text: View on GitHub
      link: https://github.com/lrivallain/precursor

features:
  - icon: 🧵
    title: Topic-scoped conversations
    details: >-
      Long-lived, tree-organized threads that each carry their own history and
      context. Link a topic to a GitHub issue and its body, comments, and labels
      become live context — newer updates outweigh older ones.
    link: /features/topics
    linkText: About topics
  - icon: 💬
    title: Quick chats
    details: >-
      Throwaway conversations for when you just need an answer fast. Streaming
      replies, markdown, mermaid diagrams, and code highlighting out of the box.
    link: /features/chats
    linkText: About chats
  - icon: 🎙️
    title: Live meeting assistant
    details: >-
      Record a meeting, transcribe it with speaker labels via Azure Speech, and
      get live insights, Q&A, and an editable summary you can post to a topic.
      Audio is never stored.
    link: /features/live-sessions
    linkText: About live sessions
  - icon: 🤖
    title: Autonomous agents
    details: >-
      Hand a long-running task to a Copilot SDK agent attached to a topic or
      chat, and follow its progress in a workflow-style timeline. Opt-in and off
      by default.
    link: /features/agents
    linkText: About agents
  - icon: 🗂️
    title: Workspaces & files
    details: >-
      Point the assistant at a git clone or a local directory and let it browse
      and edit files inside a path-traversal-proof sandbox.
    link: /features/workspaces
    linkText: About workspaces
  - icon: 📋
    title: Kanban board
    details: >-
      Track the GitHub issues linked to your topics on a board that spans your
      projects — a bird's-eye view of work in flight.
    link: /features/kanban
    linkText: About the board
  - icon: 🔌
    title: MCP, both ways
    details: >-
      Precursor is an MCP server (it exposes your conversations) and an MCP
      client (it attaches tool servers per turn) — GitHub, fetch, workspace-fs,
      a command runner, WorkIQ, and your own.
    link: /features/mcp
    linkText: About MCP
  - icon: 🧠
    title: Skills & memory
    details: >-
      Reusable /slash prompt presets stored as SKILL.md files (interoperable with
      the Copilot CLI) plus long-term memory injected into every conversation.
    link: /features/skills-memory
    linkText: About skills & memory
  - icon: ⏰
    title: Scheduler & reminders
    details: >-
      Put any topic or agent on a cadence, gate a run behind a cheap MCP probe
      with /guard, or set a one-shot reminder that resurfaces the thread.
    link: /features/scheduler
    linkText: About the scheduler
  - icon: 🧩
    title: Extensible by design
    details: >-
      A backend entry-point plugin contract plus a frontend extension registry
      let third parties add routers, panels, and MCP tools without forking core.
    link: /features/plugins
    linkText: About plugins
  - icon: 🧰
    title: Bring your own model
    details: >-
      GitHub Copilot, GitHub Models, Azure AI Foundry, OpenAI, Mistral, Hugging
      Face, Ollama — or a deterministic mock provider for offline development.
    link: /guide/configuration
    linkText: Configure a provider
  - icon: 🔒
    title: Local-first & private
    details: >-
      A single-user app that binds to 127.0.0.1 by default. Secrets live in the
      local DB and are never echoed back by the API. No Node.js runtime in
      production.
    link: /reference/architecture
    linkText: How it works
---

<div style="max-width: 1152px; margin: 4rem auto 0; padding: 0 24px;">

## One process, everything included

Precursor is a small, opinionated assistant for tracking work-in-progress
conversations alongside the issues they belong to. In production it is a **single
uvicorn worker**: FastAPI serves the JSON API and mounts the pre-built React SPA
from the same process — no separate Node.js runtime, no orchestration.

```bash
# Run the latest published build with zero setup:
uvx precursor
```

Prefer to hack on it? The whole dev stack — backend hot-reload plus Vite HMR —
starts with one command:

```bash
uv run precursor --dev
```

Head to the [installation guide](/guide/installation) to get set up, or browse
the [feature guides](/features/) to see everything Precursor can do.

</div>
