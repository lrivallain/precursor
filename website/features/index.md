---
title: Feature guides
---

# Feature guides

One page per feature. Start with the sections, then the capabilities they share.

## Sections

| Section | What it's for |
| --- | --- |
| 🧵 [**Topics**](/features/topics) | Long-lived, nested threads, optionally linked to a GitHub issue used as live context. |
| 💬 [**Chats**](/features/chats) | Quick, throwaway conversations. |
| 🎙️ [**Live sessions**](/features/live-sessions) | Meeting transcription with live insights, Q&A and a templated summary. |
| 🤖 [**Agents**](/features/agents-mode) | Autonomous Copilot SDK agents, monitored from a dashboard (opt-in). |
| 🔗 [**Workflows**](/features/workflows) | Agents chained into a pipeline with gates and approvals, run on demand, on a schedule or by webhook (opt-in). |
| 🗂️ [**Files**](/features/workspaces) | Git clones and folders the assistant can browse and edit. |
| ✨ [**IQ**](/features/iq) | Questions over everything in Precursor, answered with cited sources. |
| 📋 [**Kanban**](/features/kanban) | A GitHub Projects board (plugin). |

Navigation: drag sections in the sidebar to reorder them (remembered across
reloads and both navigation styles). Agents and Workflows open on an
**Overview** dashboard. The browser tab title names the open item and section.

## Working in conversations

| Feature | What it does |
| --- | --- |
| [Topic summary](/features/topic-summary) | Editable status brief (status, actions, key facts) above a topic. |
| [Collections](/features/collections) | Separate groups of topics, each with its own repository and default role. |
| [Step by step](/features/step-by-step) | Split a long ask into a tree of steps, approve each, combine the result. |
| [Attachments](/features/attachments) | Images as vision input; PDF, DOCX, PPTX and text files extracted. |
| [Context compression](/features/context-compression) | Automatic trimming of old tool results and images; `/compact` summarises. |
| [Timeline & rewind](/features/rewind) | Jump between turns; rewind or edit-and-resend with undo. |
| [Skills, roles & memory](/features/skills-memory) | `/slash` prompt presets (`SKILL.md`), personas, long-term memory. |
| [Tool approvals](/features/approvals) | Which tool calls run on their own and which wait for you. |

## Automation and tools

| Feature | What it does |
| --- | --- |
| [Scheduler & reminders](/features/scheduler) | Run topics, agents and workflows on a cadence, `/guard` them with cheap probes, set reminders. |
| [MCP (both ways)](/features/mcp) | Built-in and custom tool servers; Precursor also serves its data over MCP. |
| [Command runner](/features/command-runner) | Run bash / python / node in a Docker jail or locally. |
| [OpenAI-compatible endpoint](/features/openai-endpoint) | Use Precursor's configured models from VS Code or any OpenAI client. |
| [Import & export](/features/transfer) | Share agents and workflows as YAML. |
| [Definition files](/features/definitions) *(WIP)* | Agents, workflows, roles and summary templates as YAML files in git. |

## App and operations

| Feature | What it does |
| --- | --- |
| [Background app](/features/background-app) | Login item, menu-bar icon, in-place updates. |
| [Storage & retention](/features/storage) | Retention sweeps, cleanup cockpit, database compaction. |
| [Plugins](/features/plugins) | Packages that add sections, tools, settings and routes; see the [catalogue](/plugins/). |
| [Phone & tablet layout](/features/mobile) | Drawer sidebar and touch layout below 768 px. |
| [Keyboard shortcuts](/features/keyboard-shortcuts) | Press `?` in the app for the list. |
| [Accessibility](/features/accessibility) | Theme and reading fonts, including dyslexia-friendly ones. |

Internals: [architecture](/reference/architecture) and [stack](/reference/stack).
