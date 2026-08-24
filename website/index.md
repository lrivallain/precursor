---
layout: home

hero:
  name: Precursor
  text: Opinionated approach to work follow-up, built as an AI assistant
  tagline: >-
    A single, local-first app that keeps track of your work and/or personal topics with AI conversations enhancement, live meetings transcript, autonomous agents, a workflow engine, MCP integration and more.
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
      context. Optionally link a topic to a GitHub issue and its body, comments,
      and labels become live context — newer updates outweigh older ones.
    link: /features/topics
    linkText: About topics
  - icon: 💬
    title: Quick chats
    details: >-
      Simple conversations for when you just need an answer fast. Streaming
      replies, markdown, diagrams generation, and code highlighting out of the box.
    link: /features/chats
    linkText: About chats
  - icon: 🎙️
    title: Live meeting assistant
    details: >-
      Transcribe a meeting with speaker labels via Azure Speech, and
      get live insights, Q&A, notes and an editable summary you can post to a topic.
    link: /features/live-sessions
    linkText: About live sessions
  - icon: 🤖
    title: Autonomous agents
    details: >-
      Hand complex, long-running tasks to Copilot SDK agents that run in the background, and monitor the whole fleet from a control-tower dashboard that surfaces the ones that need you.
    link: /features/agents-mode
    linkText: About agents
  - icon: 🔗
    title: Workflows
    details: >-
      Chain independent agents into a reusable pipeline — research → draft →
      review — that the workflow coordinates in the background, with schedule and
      webhook triggers.
    link: /features/workflows
    linkText: About workflows
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
      the Copilot CLI) plus long-term memory injected into conversation.
    link: /features/skills-memory
    linkText: About skills & memory
  - icon: ⏰
    title: Scheduler & reminders
    details: >-
      Put any topic, agent or workflow on a cadence or set one-shot reminders that resurface the thread on a specified date/time.
    link: /features/scheduler
    linkText: About the scheduler
  - icon: 🧰
    title: Bring your own model
    details: >-
      GitHub Copilot, Azure AI Foundry, OpenAI, Mistral, Hugging Face, Ollama —
      or a deterministic mock provider for offline development.
    link: /guide/configuration
    linkText: Configure a provider
  - icon: 🔒
    title: Local-first & private
    details: >-
      A single-user app that binds to localhost by default. Secrets live in the
      local DB and are never echoed back by the API.
    link: /reference/architecture
    linkText: How it works
---

<div style="max-width: 1152px; margin: 4rem auto 0; padding: 0 24px;">

## Let's go!

Precursor is an AI assistant, built with an opinionated approach to
tracking work-in-progress conversations alongside the GitHub issues they may
belong to.

```bash
# Run the latest published build with zero setup:
uvx precursor-ai
```

Prefer to hack on it? The whole dev stack — backend hot-reload plus Vite HMR —
starts with one command:

```bash
uv run precursor --dev
```

Head to the [installation guide](/guide/installation) to get set up, or browse
the [feature guides](/features/) to see everything Precursor can do.

</div>
