---
title: Quick start
---

# Quick start

From a fresh [install](/guide/installation) to your first conversation.

## 1. Open the app

Open <http://localhost:8000> (or the port `precursor service status` reports).
The **home** launcher shows a card per section.

<Screenshot src="/screenshots/home.png" alt="The Precursor home launcher with section cards" />

## 2. Create a topic

**Topics → New topic**, give it a name. Optionally **link a GitHub issue** (URL
or number) or create one: its body, comments and labels are re-read as context
on every turn, newest comments first.

<Screenshot src="/screenshots/topics.png" alt="An onboarding topic beside the collection picker and topic tree" />

## 3. Chat

Send a prompt; the reply streams with markdown, code and `mermaid` rendering, and
tool calls appear inline.

| Try | Does |
| --- | --- |
| `/` | Slash commands: skills, memory, GitHub actions, `/notes`, … |
| Attach a file | Images as vision input; PDF / DOCX / PPTX as text ([attachments](/features/attachments)) |
| ⌘K / Ctrl+K | Command palette: jump anywhere, search; **Tab** to [ask a question](/features/iq#ask-from-k) |
| ⌘⇧K / Ctrl+Shift+K | Open [IQ](/features/iq#the-iq-section) with the selected text |
| `C` / `N` / `Esc` / `?` | Focus composer / new item / stop streaming / all [shortcuts](/features/keyboard-shortcuts) |

## Other sections

| Section | Quick start | Needs first |
| --- | --- | --- |
| 🤖 **Agents** | [Run a task in the background](/guide/quick-start/agents) | the Copilot CLI (one click) |
| 🎙️ **Live sessions** | [Record and summarise a meeting](/guide/quick-start/live-sessions) | Azure Speech |
| 🔗 **Workflows** | [Chain agents into a pipeline](/guide/quick-start/workflows) | Agents enabled |
| 📋 **Kanban** | [Put your issues on a board](/guide/quick-start/kanban) | the Kanban plugin + a token with Projects access |
| 🗂️ **Files** | [Let the assistant edit files](/guide/quick-start/files) | — |

Next: [connect a real model](/guide/configuration), or browse the
[feature guides](/features/).
