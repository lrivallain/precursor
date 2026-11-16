---
title: Context compression
---

# Context compression

Keep long conversations inside the model's context window. **Automatic trimming** runs every
turn at no cost; **compaction** (`/compact`) summarises the conversation and only runs when you
ask.

<Screenshot src="/screenshots/context-compression.png" alt="A topic transcript with a Context compacted divider, the older messages dimmed above it, and the conversation stats panel showing the context window bar and a Compact context button" caption="After /compact, the older messages stay visible but dimmed; the model works from the summary." />

## Automatic trimming

Applied to what the model sees, never to the transcript:

- **Inline images in tool results** (e.g. base64 Playwright screenshots, ~150,000 characters
  each) become placeholders like `[binary omitted: ~138 KB]`.
- **Tool results over 2,000 characters older than the last 5 user turns** are sent as a short
  preview; the model can call the tool again. Set the number of turns in **Settings → Model →
  Prompt budgeting → Keep tool results in full for the last N turns**
  (`llm_tool_result_keep_turns`; `0` = always full).

**Max tokens per tool result** and **Max input tokens per request** still apply afterwards;
past the budget, the oldest turns are dropped without a summary.

## Compacting a topic or chat

Run **`/compact [focus]`** or click **Compact context** in the conversation stats panel. The
model writes a summary (goal, key facts and decisions with names/IDs/numbers/URLs verbatim,
work done, open questions, current state) saved as a **compaction marker**. From the next turn
the model sees the summary plus later messages.

- `/compact keep the SKU comparison and the pricing figures` steers it.
- Older messages stay visible, dimmed. **Show summary** on the marker shows what the model
  works from.
- **Undo** on the marker restores the full history.
- Compacting again folds the previous summary in.
- Not available while a reply is generating.
- Uses the conversation's model; tokens count under `/compact` in **Settings → Usage stats**.

### Knowing when to compact

The stats panel's **context window** bar splits the last turn's usage:

| Segment | Contains |
| --- | --- |
| System prompt | Role, instructions, skills, memories |
| System tools | Built-in tools (the `precursor` server, the approval tool) |
| MCP tools | Other enabled MCP servers' tool definitions; often the largest. Disable servers you don't need. |
| Messages | The conversation, tool results included |
| Free space / Buffer | What's left; the buffer is the last 20% of the window |

<Screenshot src="/screenshots/context-breakdown.png" alt="The conversation stats panel with a segmented context window bar and a legend listing system prompt, system tools, MCP tools, messages, free space and buffer with their share and token count" caption="The context window, broken down by what fills it." />

Values marked `≈` are Precursor's estimates (providers report one total); a linked issue's
body isn't counted. Past **80%**, **Compact context** turns amber (also on the collapsed rail).

<Screenshot src="/screenshots/context-compression-nudge.png" alt="The conversation stats panel with the context window bar at 87% and an amber call-out: Context is 87% full, with a Compact context button" caption="Past 80% of the window, the stats panel suggests compacting." />

After a compaction, until the next turn, **Next turn (estimate)** shows the history to be sent,
what trimming saved, and how many messages the marker covers (system prompt and tools
excluded).

[Agents](/features/agents-mode/running) show the same bar in the Insights panel, from the
Copilot SDK's split, with a single **System tools** row.

<Screenshot src="/screenshots/agents-context.png" alt="The agent Insights panel with the same segmented context window bar and legend" caption="The agent Insights panel, with the SDK's own breakdown." />

## Compacting an agent

The Copilot SDK compacts [agent](/features/agents-mode) history on its own when the window runs
low. To compact now, run **`/compact [focus]`** in the agent's composer or click **Compact
context** under **Usage** in the insights sidebar, while the agent is at rest. Every compaction
shows as a **context compacted** marker in the timeline with tokens before/after; hover it for
the summary.

## See also

- [Attachments](/features/attachments#how-much-of-a-document-the-model-sees)
- [Storage & retention](/features/storage): pruning stored tool results (separate from what
  the model sees).
- [Configuration](/reference/configuration#llm-provider): prompt budgeting settings.
