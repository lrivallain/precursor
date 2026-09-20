---
title: Topic summary
---

# Topic summary

A **topic summary** is the short status brief that sits above the transcript:
where the topic stands, the actions still open, and the handful of facts needed
to act. It is written by the model and **owned by you** — once you edit it, no
refresh can silently overwrite your text.

The slim **Summary bar** beneath the topic header is always available, even
before a brief exists. Its centered chevron expands or collapses the panel;
refresh, edit and delete controls stay inside the expanded area. When empty,
open it and choose **Generate summary**. Merely opening it does not call the model.

The **document icon in the topic header** and `/show-summary` remain shortcuts.
By default a topic has **no summary at all**: nothing is generated until you ask.
Pending suggestions remain visible as a small count on the collapsed bar.

<Screenshot
  src="/screenshots/topic-summary-collapsed.png"
  alt="A slim summary toggle below the topic header, with a centered chevron and a pending-change count"
  caption="Collapse the brief to keep the conversation in focus; the Summary bar remains within reach."
/>

<Screenshot
  src="/screenshots/topic-summary.png"
  alt="The topic summary panel above a transcript, with a proposed update listed change by change"
  caption="A hand-edited brief with a fresh proposal: every change is accepted or refused on its own."
/>

## Generating one

`/update-summary` (or the ↻ button in the panel) writes a brief from the
topic's **conversation**, its [`/notes` scratchpad](/features/topics) and the
names of the files [attached](/features/attachments) to it. Add an instruction
to steer it — `/update-summary focus on what blocks the release`.

The prompt uses the latest 40 non-tool messages (up to 2,000 characters each),
up to 4,000 characters of scratchpad notes, and the latest 100 attached filenames.
It does not independently read attachment contents. Empty or failed model
responses leave the existing brief unchanged and display an error.

The first generated brief uses three sections, so it stays scannable and
diffable:

```markdown
## Status
- Release branch cut, QA in progress

## Actions
- [ ] Ask Bob for the signing cert
- [x] Tag rc2

## Key information
- Ship window closes Friday
```

Content is deliberately limited to **real actions, updates and information** —
no retelling of the conversation.

Refreshing an existing brief is a **minimal-update pass**, not a rewrite. The
model is instructed to preserve wording, formatting and ordering, and to change
only material facts or actions supported by explicit updates. Repeated facts,
paraphrases and missing mentions in recent history should not trigger changes.
With nothing substantive to update, it is asked to return the existing text
verbatim, including for a brief you have not edited yourself. You can still
explicitly request a rewrite or reorganization in the command's instruction.
This is prompt guidance, not a semantic filter; review suggestions before
accepting them.

## Editing, and what happens next

Check or uncheck an action **directly in view mode** to mark it done or reopen
it. The checkbox change is saved immediately; task lists elsewhere in the
conversation remain read-only.

Double-click the summary text to edit its Markdown **at the clicked position**,
or use the ✎ button. Links, action checkboxes and other controls keep their
normal behavior. Changes **autosave after a
1.2-second pause**, with **Saving…**, **Saved**, or **Not saved** feedback.
You can keep typing while a save is in flight. **Done** returns to view mode;
leaving the editor, collapsing the panel, or navigating to another topic also
flushes pending edits. There is no separate Save or Cancel step.

<Screenshot
  src="/screenshots/topic-summary-editing.png"
  alt="The summary markdown editor with a Done button and Saved status"
  caption="Edit the Markdown directly; changes save automatically without leaving the editor."
/>

Both manual edits and checkbox changes mark the summary as **yours**. From then on:

- a refresh is told to treat your wording as authoritative and to keep it
  wherever it is still accurate — your edits carry more weight than the model's
  previous text;
- the new version is **never written straight in**. It arrives as a list of
  **suggested changes** — removed lines in red, added lines in green — and you
  accept or refuse each one before anything is saved, the way you review a code
  suggestion. **Apply selected** writes exactly what you ticked; **Refuse all**
  leaves the brief untouched.

Everything is persisted server-side, so the brief (and whether the panel is
expanded) survives reloads and updates other open windows immediately.
Saving an edit or appending an item discards the old proposal because it was
based on different text. A refresh with no changes creates no review.

If another window or scheduled run changes the brief while you are editing,
generating, or reviewing it, the stale write is rejected rather than overwriting
newer work. A failed save keeps your local changes visible. Use **Retry save**
after a connection error, or copy any text you want to keep and choose
**Reload latest** to explicitly discard the unsaved draft and use the latest
server version. Unsaved drafts are also cached in the current tab so a failed
save is recoverable after navigation or reload. Closing the browser tab with
unsaved edits prompts a warning.

## Slash commands

| Command | What it does |
| --- | --- |
| `/update-summary [instruction]` | Generate or refresh the brief. On an edited brief, arrives as reviewable changes. |
| `/show-summary` | Expand the summary area. |
| `/hide-summary` | Collapse it — the text is kept. |
| `/todo-summary <action>` | Add a pending action to **Actions**. |
| `/important-summary <information>` | Add a fact to **Key information** so it survives future refreshes. |

The 🗑 button asks for confirmation, then drops the summary entirely, returning
the topic to having none.

All five commands also work from a [scheduled](/features/scheduler) topic
prompt, so a recurring run can keep a brief up to date on its own — a refresh
that lands on an edited brief leaves a note in the transcript instead of
applying itself, and waits for your review.
