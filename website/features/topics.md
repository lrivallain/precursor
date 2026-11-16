---
title: Topics
---

# Topics

A **topic** is a long-lived conversation you return to over days or weeks, optionally
**linked to a GitHub issue** that feeds every turn. For a throwaway question, use a
[chat](/features/chats).

<Screenshot src="/screenshots/topics.png" alt="An onboarding topic beside the collection picker and topic tree" caption="A persistent conversation beside its topic tree, with collections keeping related work together." />

## Model per topic

The composer's model picker lists every configured provider's models and presets. The choice
(with reasoning effort and context size) is saved on the topic; **Use global default** clears
it. Replies, retries, compaction, summaries, naming and scheduled/MCP-driven turns all use it.
Side chats copy it; child topics start on the global default. An unconfigured provider shows
an error instead of falling back. Agents and workflows remain Copilot SDK-only. See
[which model gets used](/guide/configuration#which-model-gets-used).

## Issue-linked context

Link a topic to a GitHub issue (paste a URL or number, or create one from the topic). Every
turn rebuilds context from the issue's **body**, **most recent comments** and **labels**, so
edits to the issue apply immediately. The composer's GitHub actions and the shared draft panel
create or update the issue and post comments.

## Tree organization

- Nest topics under a parent. Right-click a topic in the sidebar to rename, pin, set a
  reminder, move it to another [collection](/features/collections), or archive.
- `/suggest-name` renames the topic from its conversation (as
  [chats name themselves](/features/chats#chats-name-themselves)).
- Over [MCP](/features/mcp), a topic carries a `path` (collection slug + ancestor chain,
  e.g. `client-a/csu/capacity-review`) that matches its URL.

## Addressing a topic

| Address | Example | Stable? |
| --- | --- | --- |
| Readable | `/topics/client-a/csu/cto/capacity-review` | Changes when you rename, move or re-parent the topic. |
| Permalink | `/topics/id/6f2b1c9e-…` | Never changes. Copy it from **topic settings → Permalink**. |

Opening a permalink rewrites the address bar to the readable URL. Old `/t/<uuid>` links and
pre-collection links (`/topics/csu/capacity-review`) still resolve.

### Link to an exact message

<Screenshot src="/screenshots/message-links.png" alt="A topic reply with Copy link to message in its hover action bar" caption="Copy a stable link to the exact prompt or reply, not just its discussion." />

**Copy link to message** in a message's action bar copies the permalink plus
`#message=<message-uuid>`. Opening it loads earlier pages if needed and highlights the
message; deleted or rewound messages show **This message is no longer available**. Links work
on the same Precursor instance only.

The model gets a reference index of up to 50 recent messages, so it can link to "the previous
reply" in its answers. The same action works in [chats](/features/chats#message-links) and
[agents](/features/agents-mode/running#message-links).

## Summary, approvals, schedules

- **[Summary](/features/topic-summary)**: an editable status brief above the transcript.
- **[Tool approvals](/features/approvals)**: override the policy from the composer's shield
  pill. Pending calls appear as inline cards and as an amber badge on the sidebar row. Topic
  settings list its [approval rules](/features/approvals#rules).
- **[Scheduler](/features/scheduler)**: run a prompt on a cadence, or set a one-shot reminder.

## Side chats

Explore a tangent without adding it to the topic. Start a
[side chat](/features/chats#side-chats) from a reply's toolbar, a text selection, the **New
side chat** header button, the sidebar right-click menu, **Side chats** in the right panel, or
`/side-chat`. It knows the topic's title, description and summary, and can
[send its outcome back](/features/chats#sending-the-outcome-back-to-the-topic) as a note.

<Screenshot src="/screenshots/side-chats-panel.png" alt="A topic's right panel listing three side chats with unread, reminder and activity details" caption="The topic's side chats in its right panel: unread counts, reminders and last activity at a glance." />

## Editing replies

Click **Edit reply** (the pen) in an assistant reply's toolbar, change the Markdown, **Save**
(⌘/Ctrl+Enter) or Esc to cancel. Later turns see your version as the model's answer. An
**Edited** chip shows the original and lets you **Restore** it.

<Screenshot src="/screenshots/reply-edit.png" alt="An edited email draft with its original reply shown below in an amber block, with Restore and Hide buttons" caption="An edited reply: the Edited chip opens the model's original underneath, ready to restore." />

### Acting on a selection

Select text in an assistant reply for a small toolbar:

- **Side chat** (topics): start a [side chat](#side-chats) quoting the selection.
- **Edit**: open the reply editor with that text selected.
- **Note**: append the selection's Markdown to the [`/notes`](#under-the-composer) pad.

## Under the composer

- Streaming Markdown, Mermaid and code highlighting.
- `/` slash commands: [skills](/features/skills-memory), memory, GitHub actions, `/notes`,
  [summary](/features/topic-summary) commands.
- [Attachments](/features/attachments): images (vision) and documents.
- [MCP tools](/features/mcp), shown inline as they run.

## Watching the model think

While a reasoning model thinks, a pulsing **Thinking…** row previews its current step; click
it to read the full thinking. It stays collapsed above the answer after a reload, is kept per
tool round, and survives **Stop**.

<Screenshot src="/screenshots/thinking.png" alt="An assistant reply with its Thinking area expanded above the answer" caption="The model's thinking, opened above its answer. Collapsed, the row shows only the latest step." />

| Model | What shows |
| --- | --- |
| Gemini, GPT-5.5 and later | A streamed summary, one titled step at a time. |
| Older GPT models | Nothing (their API doesn't expose it). |
| Claude | Only when an effort is picked; the summary arrives just before the answer. |
| Non-reasoning models | No Thinking area. |

The effort picker sets how hard the model thinks. Thinking is never sent back to the model,
and is removed after the tool-result retention window (**Settings → System → Storage /
retention**, see [Storage & retention](/features/storage)).

## Copying a reply

| Button | Copies |
| --- | --- |
| **Copy message** | Plain text. |
| **Copy formatted** (envelope) | HTML that keeps its spacing when pasted into Outlook or a document. Diagrams are copied as source. |
| **Copy raw markdown** | The Markdown source. |

The same formatted copy backs **Copy** on summaries, notes, workflow outputs and
[Step by step](/features/step-by-step) final outputs.

## Deleting, clearing and stopping

- **Delete** a message from its toolbar; an undo toast gives you **5 s** (switching away
  commits it).
- **`/clear`** (or **Topic settings → Clear chat**) empties the transcript after confirmation,
  stopping any streaming reply first.
- **`/compact [focus]`** summarises the conversation to free the context window; **Undo** on
  the marker reverts it. See [context compression](/features/context-compression).
- **Rewind** to an earlier turn, or **Edit & resend** a prompt. See
  [timeline & rewind](/features/rewind).
- **Stop** keeps what streamed, marked _(stopped)_; a running tool call settles as
  **stopped**.

Chats and [workspace discussions](/features/workspaces#discussing-a-workspace) behave the same.

## Artifacts

Workspace files the topic created or you attached are listed as **Artifacts** in the right
stats area. See [Discussing a workspace](/features/workspaces#discussing-a-workspace).

## When a turn fails

A failed turn (rejected model, bad credentials, tool loop cap) is saved as a **red error
notice**. The failed prompt gets a **Retry** button that replays it with the current model and
settings, so fix the cause first (switch model, add a token in **Settings → Models**).
