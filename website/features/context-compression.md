---
title: Context compression
---

# Context compression

Long conversations fill the model's context window, especially when they carry
screenshots, file reads and search results. Precursor keeps that in check in two
ways. **Automatic trimming** is free and makes no model call. **Compaction**
summarises the conversation so far so you can keep going in the same thread.
Compaction only runs when you ask for it.

<Screenshot src="/screenshots/context-compression.png" alt="A topic transcript with a Context compacted divider, the older messages dimmed above it, and the conversation stats panel showing the context window bar and a Compact context button" caption="After /compact, the older messages stay visible but dimmed; the model works from the summary." />

## Automatic trimming

Before each turn, Precursor trims what the model sees. The transcript itself is
left untouched.

- **Inline images are dropped.** Tools such as the Playwright browser return
  screenshots as base64 text inside their result. The model can't see a picture
  through its base64, so each image becomes a placeholder such as
  `[binary omitted: ~138 KB]`. One screenshot alone is about 150,000
  characters.
- **Large tool results from older turns are shortened.** A tool result over
  2,000 characters that is older than the last **5** user turns reaches the
  model as a short preview and a note. The model can call the tool again when
  it needs the full output. Change the number of turns in **Settings → Model →
  Prompt budgeting → Keep tool results in full for the last N turns**
  (`llm_tool_result_keep_turns`). Set it to `0` to always send full results.

The per-result cap and the overall input budget (**Max tokens per tool result**,
**Max input tokens per request**) still apply after these two passes. Past the
budget, the oldest turns are dropped first, without a summary. Compaction
exists so that doesn't happen.

## Compacting a topic or chat

Type **`/compact`** in the composer, or click **Compact context** in the
**conversation stats** panel on the right of the transcript (shown unless
turned off in **Settings → Chat**). The model writes a structured
summary of the conversation with these sections:

- the goal
- key facts and decisions, with names, IDs, numbers, URLs and paths copied
  verbatim
- work done
- open questions
- where things stand

The summary is saved as a **compaction marker**, and from the next turn on the
model sees the summary plus everything after it.

- **Say what matters.** Add a focus after the command, for example
  `/compact keep the SKU comparison and the pricing figures`.
- **Nothing is deleted.** The messages above the marker stay in the
  transcript, dimmed. **Show summary** on the marker shows what the model now
  works from.
- **Undo** removes the marker, and the model sees the full history again. The
  usual undo toast gives you a few seconds to change your mind.
- **Compacting again** folds the previous summary into the new one.
- **Not while a reply is generating.** Wait for the reply to finish, or stop
  it, before you compact.

The summary is written by the conversation's model. Its token usage is counted
under `/compact` in **Settings → Usage stats**.

### Knowing when to compact

The stats panel's **context window** bar shows how much of the model's window
the last turn used. Past **80%**, the **Compact context** button turns amber
with a nudge. The collapsed stats rail shows an amber shortcut too.

<Screenshot src="/screenshots/context-compression-nudge.png" alt="The conversation stats panel with the context window bar at 87% and an amber call-out: Context is 87% full, with a Compact context button" caption="Past 80% of the window, the stats panel suggests compacting." />

After a compaction, until the next turn reports real usage, the bar shows an
estimate (marked `≈`). The **Next turn (estimate)** section shows:

- the history the next turn will send
- how much automatic trimming saved
- how many messages the latest marker covers

The estimate leaves out the system prompt and tool definitions.

## Compacting an agent

[Agents](/features/agents-mode) run on the Copilot SDK. The SDK already compacts
their history on its own when the window runs low. **`/compact [focus]`** in
the agent's composer, or **Compact context** under **Usage** in the insights
sidebar, triggers a compaction now. Only an agent at rest can be compacted:
wait for the turn to finish, or stop it, first.

Every compaction shows in the timeline as a **context compacted** marker, both
the automatic ones and yours. The marker shows the tokens before and after;
hover it to read the summary. The usage panel's context bar drops right away.

## See also

- [Attachments](/features/attachments): how much of a document the model sees.
- [Storage & retention](/features/storage): pruning old tool results from the
  database, which is a different lever from what the model sees.
- [Configuration](/reference/configuration#llm-provider): the prompt
  budgeting settings.
