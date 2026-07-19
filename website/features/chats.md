---
title: Chats
---

# Chats

**Chats** are quick, throwaway conversations — for when you just need an answer
and don't want the ceremony of a [topic](/features/topics). Type a prompt and get
going in seconds.

<Screenshot src="/screenshots/chats.png" alt="A quick chat with a streamed markdown reply and a mermaid diagram" caption="A quick chat — streaming markdown, code highlighting, and mermaid diagrams, no setup required." />

## When to use a chat vs a topic

| | Chat | Topic |
| --- | --- | --- |
| Lifespan | Throwaway | Long-lived |
| GitHub issue link | — | Optional, used as live context |
| Tree nesting | — | Yes |
| Scheduling / reminders | — | Yes |
| Best for | A one-off question | A tracked thread of work |

Reach for a **chat** to draft a message, explain an error, or brainstorm. Reach
for a **topic** when the conversation is part of ongoing work you'll return to.

## Same rich composer

Chats share the same conversation experience as topics:

- **Streaming** replies over Server-Sent Events with live markdown rendering.
- **Mermaid diagrams**, fenced code blocks, and syntax highlighting.
- **`/` slash commands**, including [skills](/features/skills-memory) and the
  memory commands (`/memory-store`, `/memory-list`, `/memory-update`).
- **[Attachments](/features/attachments)** — images as vision input, PDF / DOCX /
  PPTX text-extracted.
- **[MCP tools](/features/mcp)** — the same enabled tool servers, with tool calls
  shown inline.
- **Long-term [memory](/features/skills-memory)** is injected into chats too, so
  your standing preferences and facts follow you here as well.

## Unread badges & notifications

Chats — like topics and agents — track unread activity. When a reply arrives
while you're looking elsewhere, the chat's row shows an unread count, the sidebar
tab highlights, and (when notifications are enabled and the window is unfocused) a
browser notification fires. Opening the chat clears its badge.

## Lazy-loaded history

A chat doesn't load its entire transcript up front — it fetches the most recent
page (50 messages) and pulls older ones in as you scroll toward the top,
preserving your scroll position.
