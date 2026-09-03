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
- **Failed turns** surface as a red error notice with a **Retry** button on the
  prompt that failed — see
  [when a turn fails](/features/topics#when-a-turn-fails).

## Chats name themselves

A new chat starts as **"New chat"** — which, after a busy afternoon, leaves a
sidebar full of rows you can't tell apart. So Precursor names it for you: as soon
as you send the first message, a short side request derives a title from what you
asked and renames the chat in place.

It runs **alongside** the answer rather than after it, so the name usually lands
while the reply is still streaming — you never wait on it. If the model is
unreachable or returns something unusable, the placeholder simply stays; naming
never delays or fails the turn it rides along with.

A title **you** set always wins. Rename a chat — from the sidebar's right-click
menu, the header, or `/rename` — and auto-naming steps aside for good, even if a
naming request was already in flight.

Only the title changes. Chats are addressed by a stable id in their URL, so a
rename never breaks a link you've already opened or shared.

### `/suggest-name`

To re-name a conversation later — the opening question turned out not to be the
point, or you want a tidier title before archiving — run:

```
/suggest-name
```

It reads the conversation so far and renames it. Available in both **chats** and
**[topics](/features/topics)** (topic slugs are left untouched too, so existing
links keep resolving).

### Turning it off

**Settings → Chat → Auto-naming** disables it, and lets you point naming at a
specific model. Naming is a one-line request, so a small fast model is usually
the better choice; leave it on *Use default chat model* to reuse your main one.
`/suggest-name` keeps working either way.

## Unread badges & notifications

Chats — like topics and agents — track unread activity. When a reply arrives
while you're looking elsewhere, the chat's row shows an unread count, the sidebar
tab highlights, and (when notifications are enabled and the window is unfocused) a
browser notification fires. Opening the chat clears its badge.

Right-click a chat in the left sidebar to rename, pin, set a reminder, open
`/notes`, or archive it.
