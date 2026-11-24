---
title: Chats
---

# Chats

**Chats** are quick, throwaway conversations: type a prompt and go. Use a
[topic](/features/topics) for tracked work you'll return to.

<Screenshot src="/screenshots/chats.png" alt="A quick chat with a streamed markdown reply and a mermaid diagram" caption="A quick chat — streaming markdown, code highlighting, and mermaid diagrams, no setup required." />

| | Chat | Topic |
| --- | --- | --- |
| Lifespan | Throwaway | Long-lived |
| GitHub issue link | — | Optional, live context |
| Tree nesting | — | Yes |
| Scheduled prompts | — | Yes |

## Same composer as topics

Chats share the topic conversation experience. Specifics:

- **Model picker**: models and presets of every configured provider, grouped by provider,
  including several Azure AI Foundry deployments on one endpoint. The choice is saved with
  effort and context size; **Use global default** resets it. See
  [which model gets used](/guide/configuration#which-model-gets-used).
- **Presets → Efficiency / Balanced / Intelligence** let the engine pick from that category's
  ordered presets. **Optional model alternatives** (Settings → Presets) retry a missing model
  or rejected effort/context with the next preset; see
  [optional model alternatives](/guide/configuration#optional-model-alternatives). Retries
  never replay streamed output.
- Streaming Markdown, Mermaid, code highlighting, and the model's
  [thinking](/features/topics#watching-the-model-think).
- `/` commands, including [skills](/features/skills-memory) and memory (`/memory-store`,
  `/memory-list`, `/memory-update`); long-term memory is injected too.
- [Attachments](/features/attachments) and [MCP tools](/features/mcp).
- [Failed turns](/features/topics#when-a-turn-fails) with **Retry**.
- [Adding to a running turn](/features/topics#adding-to-a-running-turn): send a message
  while the reply is still coming, and the model takes it into account at its next step.
- `/compact` ([context compression](/features/context-compression)) and
  [timeline & rewind](/features/rewind).
- Delete with undo, `/clear`, Stop: see
  [deleting, clearing and stopping](/features/topics#deleting-clearing-and-stopping).
- [Editing replies](/features/topics#editing-replies) and selection actions.

### Compact composer

When the model, effort, context, approval and role pickers don't fit on one row (narrow
window, [workspace discussion](/features/workspaces#discussing-a-workspace), phone), they
shrink to icons. The value moves to the tooltip and accessible name ("Model: GPT-5 ·
Medium"). Applies to topics, chats, agents and the start screen.

## Starting a chat

**Start a new chat** (or the *New chat* card on home) is a full composer: prompt, model,
[role](/features/skills-memory), skills with `/`, and [attachments](/features/attachments).
Only skills are suggested after `/` there; built-in commands like `/clear` need an open chat.
Attaching a file creates the chat at once; it is deleted if you leave without sending.

## Chats name themselves

On the first message, a side request names the chat from your prompt, in parallel with the
reply. If it fails, the chat keeps its placeholder. A title you set (sidebar, header, or
`/rename`) always wins. Only the title changes, never the slug.

- **`/suggest-name`** renames a chat or topic from the conversation so far (slugs untouched).
- **Settings → Chat → Auto-naming** turns it off or picks a dedicated (preferably small, fast)
  model. `/suggest-name` works either way.

### Message links

**Copy link to message** in a message's action bar copies:

```text
/chats/id/<chat-uuid>#message=<message-uuid>
```

It survives title and slug changes; `/chats/<slug>` addresses still work. Behaviour matches
[topic message links](/features/topics#link-to-an-exact-message). Promoting or deleting the
chat removes its chat address.

## Unread badges & notifications

A reply that arrives while you're elsewhere adds an unread count on the chat's row, highlights
the sidebar tab, and (when enabled and the window is unfocused) fires a browser notification.
**Settings → Chat → Play a sound when a reply finishes** adds a short pop (off by default;
stopped or failed turns stay silent).

Right-click a chat in the sidebar to rename, pin, set a reminder, open `/notes`, or archive.

## Tool approvals

Each chat can set its own [tool approval policy](/features/approvals) (**manual**,
**balanced**, **autonomous**) from the composer's shield pill. Pending calls show as inline
cards and an amber badge on the sidebar row. Chat settings list its
[approval rules](/features/approvals#rules).

## Side chats

A **side chat** is a chat started from a [topic](/features/topics) for a tangent, linked to
that topic.

<Screenshot src="/screenshots/side-chats.png" alt="A side chat quoting the topic reply it was started from, with a back-link to the topic in the header" caption="A side chat started from a reply: the reply is quoted on top and the header links back to the topic." />

| Start from | How | Quotes |
| --- | --- | --- |
| A reply | **Start a chat from this reply** in its toolbar | The reply |
| Part of a reply | Select text → **Side chat** | The selection |
| The topic | **New side chat** in the header, sidebar right-click, or **New** under **Side chats** in the right panel | — |
| The composer | `/side-chat [first message]` | — |

- Every turn gets the topic's **title**, **description** and
  **[summary](/features/topic-summary)** as background. A quoted reply is a copy: it stays if
  the original is [rewound](/features/rewind) or deleted.
- It starts with the topic's [role](/features/skills-memory) and model, and
  [names itself](#chats-name-themselves) on its first message.
- An unused side chat is deleted when you leave it (kept once it has a message, a custom title,
  reminder, description, pin or notes).
- Navigation: the header's back arrow returns to the topic; **Open in topic** on the quote
  scrolls to the reply; replies show chips for their side chats; the topic's right panel lists
  them with unread count, reminder, message count and last activity. In the Chats list a side
  chat has a split icon.

### Sending the outcome back to the topic

Click **Send to topic** (merge icon) in the header, or `/send-to-topic [focus]`. The model
drafts a short note (what was explored, conclusions, follow-ups); you edit it, then confirm to
file it in the topic with a link back to the side chat.

### Archiving and deleting

- Archiving a topic asks whether to archive its open side chats too. Restoring the topic
  restores those archived with it.
- Deleting the topic keeps its side chats, unlinked.
- **Promoting** a side chat (from its settings) creates a **sub-topic** of its topic, keeping
  any quoted reply as context. If the topic was deleted, it becomes a top-level topic.
- Promoting an ordinary chat asks for the **target collection** and **parent topic**.

## Artifacts and workspaces

Workspace files the chat created or you attached are listed as **Artifacts** in the right
stats area; click one to open the workspace with the chat beside it. Chats started from a
workspace's **Discuss** button are ordinary chats. See
[workspace artifacts](/features/workspaces#artifacts).

<Screenshot src="/screenshots/chat-artifacts.png" alt="A chat whose stats area lists two workspace files under Artifacts" caption="The Artifacts section sits under the reminder in the chat's stats area." />
