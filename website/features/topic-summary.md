---
title: Topic summary
---

# Topic summary

A short status brief above a topic's transcript (status, open actions, key facts), written by
the model and **owned by you**: once you edit it, refreshes arrive as suggestions you accept or
reject.

<Screenshot
  src="/screenshots/topic-summary-collapsed.png"
  alt="A slim summary toggle below the topic header, with a centered chevron and a pending-change count"
  caption="Collapse the brief to keep the conversation in focus; the Summary bar remains within reach."
/>

<Screenshot
  src="/screenshots/topic-summary.png"
  alt="The topic summary panel above a transcript, with a proposed update listed change by change"
  caption="Accept or reject each change immediately, and resize the panel to review more at once."
/>

## Generating one

A topic has no summary until you ask. Click the **document icon** in the topic header (or
`/show-summary`), then **Generate summary**; or run `/update-summary [instruction]`
(e.g. `/update-summary focus on what blocks the release`). Once a brief exists, a slim
**Summary bar** under the header expands or collapses it; drag its bottom divider (or focus it
and use **↑/↓**, **Home/End**) to resize.

The model reads:

| Source | Limit |
| --- | --- |
| Conversation | Latest 40 non-tool messages, 2,000 characters each |
| [`/notes`](/features/topics#under-the-composer) scratchpad | 4,000 characters |
| [Attachments](/features/attachments) | Latest 100 file **names** (not contents) |

The first brief has three sections:

```markdown
## Status
- Release branch cut, QA in progress

## Actions
- [ ] Ask Bob for the signing cert
- [x] Tag rc2

## Key information
- Ship window closes Friday
```

A refresh (↻ or `/update-summary`) is a **minimal update**: the model is told to keep wording
and order and change only material facts. With nothing new, the bar shows **"Summary
refreshed — no changes"**. Ask for a rewrite explicitly in the instruction if you want one. A
failed or empty reply leaves the brief unchanged.

## Editing

- Tick or untick an action **in view mode**; it saves at once.
- Double-click the text (or ✎) to edit the Markdown at that spot. Edits **autosave** after
  1.2 s (**Saving… / Saved / Not saved**). **Done** returns to view mode.

<Screenshot
  src="/screenshots/topic-summary-editing.png"
  alt="The summary markdown editor with a Done button and Saved status"
  caption="Edit the Markdown directly; changes save automatically without leaving the editor."
/>

Once you've edited or ticked anything, the summary is **yours**:

- Refreshes treat your wording as authoritative.
- A new version is never written straight in. Changes appear **in place**, removed lines in red
  and added in green, each with **✔ Accept** / **✗ Reject**, saved immediately. **Accept all**
  / **Reject all** act on what's still pending.
- Saving an edit discards a pending proposal, since it was based on older text.

The brief and its expanded state are stored server-side and sync across windows. A write based
on stale text (another window, a scheduled run) is rejected rather than overwriting newer work:
use **Retry save**, or **Reload latest** to drop your draft. Unsaved drafts are cached in the
tab, and closing it with unsaved edits warns you.

## Slash commands

| Command | What it does |
| --- | --- |
| `/update-summary [instruction]` | Generate or refresh. On an edited brief, arrives as reviewable changes. |
| `/show-summary` | Expand the summary. |
| `/hide-summary` | Collapse it (the text is kept). |
| `/todo-summary <action>` | Add an open action to **Actions**. |
| `/important-summary <information>` | Add a fact to **Key information** so refreshes keep it. |

The 🗑 button deletes the summary after confirmation.

All five commands work in [scheduled](/features/scheduler) topic prompts. A scheduled refresh
on an edited brief leaves a note in the transcript and waits for your review.
