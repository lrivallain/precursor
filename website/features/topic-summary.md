---
title: Topic summary
---

# Topic summary

A **topic summary** is the short status brief that sits above the transcript:
where the topic stands, the actions still open, and the handful of facts needed
to act. It is written by the model and **owned by you** — once you edit it, no
refresh can silently overwrite your text.

Open it from the **document icon in the topic header**, or with
`/show-summary`. By default a topic has **no summary at all**: nothing is
generated until you ask for it.

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

The brief always has the same three sections, so it stays scannable and
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

## Editing, and what happens next

Press the ✎ button to edit the markdown directly; saving marks the summary as
**yours**. From then on:

- a refresh is told to treat your wording as authoritative and to keep it
  wherever it is still accurate — your edits carry more weight than the model's
  previous text;
- the new version is **never written straight in**. It arrives as a list of
  **suggested changes** — removed lines in red, added lines in green — and you
  accept or refuse each one before anything is saved, the way you review a code
  suggestion. **Apply selected** writes exactly what you ticked; **Refuse all**
  leaves the brief untouched.

Everything is persisted server-side, so the brief (and whether the panel is
expanded) survives reloads and follows you between windows.

## Slash commands

| Command | What it does |
| --- | --- |
| `/update-summary [instruction]` | Generate or refresh the brief. On an edited brief, arrives as reviewable changes. |
| `/show-summary` | Expand the summary area. |
| `/hide-summary` | Collapse it — the text is kept. |
| `/todo-summary <action>` | Add a pending action to **Actions**. |
| `/important-summary <information>` | Add a fact to **Key information** so it survives future refreshes. |

The 🗑 button drops the summary entirely, returning the topic to having none.

All five commands also work from a [scheduled](/features/scheduler) topic
prompt, so a recurring run can keep a brief up to date on its own — a refresh
that lands on an edited brief leaves a note in the transcript instead of
applying itself, and waits for your review.
