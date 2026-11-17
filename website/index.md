---
layout: home

hero:
  name: Precursor
  text: Opinionated approach to work follow-up, built as an AI assistant
  tagline: >-
    One local-first app for your work topics, chats and meetings, with
    autonomous agents, workflows, workspaces and MCP tools to act on them.
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
    title: Topics
    details: >-
      Long-lived, tree-organized threads with an editable summary. Link one to a GitHub issue and its body, comments and labels become live context.
    link: /features/topics
    linkText: About topics
  - icon: 💬
    title: Chats
    details: >-
      Quick conversations with streaming, markdown, diagrams and code highlighting. Pick the provider and model per conversation.
    link: /features/chats
    linkText: About chats
  - icon: 🎙️
    title: Live meetings
    details: >-
      Transcribe with speaker labels (Azure Speech) and get live insights, suggested answers, notes and a templated summary to file in a topic.
    link: /features/live-sessions
    linkText: About live sessions
  - icon: 🤖
    title: Autonomous agents
    details: >-
      Hand long-running tasks to Copilot SDK agents in the background and follow the fleet from a control tower.
    link: /features/agents-mode
    linkText: About agents
  - icon: 🔗
    title: Workflows
    details: >-
      Chain agents into reusable pipelines with quality gates, human approvals, and schedule or webhook triggers.
    link: /features/workflows
    linkText: About workflows
  - icon: ✅
    title: Step by step
    details: >-
      Split a long ask into a tree of steps, validate each in its own chat, and consolidate one answer.
    link: /features/step-by-step
    linkText: About step plans
  - icon: 🗂️
    title: Workspaces & files
    details: >-
      Let the assistant browse and edit a git clone or local folder inside a sandbox.
    link: /features/workspaces
    linkText: About workspaces
  - icon: 🎨
    title: Canvases
    details: >-
      Illustrate a concept right in the reply: interactive simulations, editable draw.io diagrams, Mermaid flows and SVG, kept per conversation.
    link: /features/canvas
    linkText: About canvases
  - icon: 🔌
    title: MCP, both ways
    details: >-
      An MCP server exposing your conversations, and an MCP client loading tools on demand: GitHub, fetch, workspaces, a command runner, WorkIQ and your own.
    link: /features/mcp
    linkText: About MCP
  - icon: 🛡️
    title: Tool approvals
    details: >-
      Per conversation: run tools freely, only read-only ones, or always ask first.
    link: /features/approvals
    linkText: About approvals
  - icon: 🔎
    title: Precursor IQ
    details: >-
      Ask about your own topics, chats, agents and meetings and get cited answers.
    link: /features/iq
    linkText: About Precursor IQ
  - icon: 🧠
    title: Skills & memory
    details: >-
      /slash prompt presets as SKILL.md files, roles, and long-term memory.
    link: /features/skills-memory
    linkText: About skills & memory
  - icon: ⏰
    title: Scheduler & reminders
    details: >-
      Run topics, agents or workflows on a cadence, or resurface a thread at a set time.
    link: /features/scheduler
    linkText: About the scheduler
  - icon: 📋
    title: Kanban board
    details: >-
      The GitHub issues linked to your topics on one board across projects. Ships as a plugin.
    link: /features/kanban
    linkText: About the board
  - icon: 🧰
    title: Bring your own model
    details: >-
      GitHub Copilot, Azure AI Foundry, OpenAI, Mistral, Hugging Face, Ollama, or a mock provider offline. Expose them to other clients through an OpenAI-compatible endpoint.
    link: /guide/configuration
    linkText: Configure a provider
  - icon: 🚦
    title: Runs in the background
    details: >-
      One-command install, start at login, a menu-bar icon, and self-update from the Releases or Nightly channel.
    link: /features/background-app
    linkText: About the background app
  - icon: 🔒
    title: Local-first & private
    details: >-
      Single-user, bound to localhost by default. Secrets stay in the local database and are never echoed by the API.
    link: /reference/architecture
    linkText: How it works
---

<div style="max-width: 1152px; margin: 4rem auto 0; padding: 0 24px;">

## Let's go!

Precursor is a single-user AI assistant that runs on your machine and keeps
your work, its context and the agents acting on it in one place.

Installing it is one command — it sets Precursor up, starts it, and brings it
back after every reboot:

::: code-group

```bash [macOS / Linux]
curl -fsSL https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.sh | sh
```

```powershell [Windows]
irm https://raw.githubusercontent.com/lrivallain/precursor/main/scripts/install.ps1 | iex
```

:::

Then open the URL it prints. That's the whole setup: no clone, no build step, no
database to create.

See the [installation guide](/guide/installation) for other options, or browse
the [feature guides](/features/).

</div>
