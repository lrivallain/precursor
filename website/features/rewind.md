---
title: Timeline & rewind
---

# Timeline & rewind

Skim a long conversation from a **timeline**, and **rewind** it to an earlier turn when it went
wrong. Works in [topics](/features/topics), [chats](/features/chats) and
[agent sessions](#in-agent-sessions).

<Screenshot src="/screenshots/rewind-timeline.png" alt="A chat scrolled back to an earlier turn, its prompt marked with a short blue bar, with a column of short dashes on the right edge of the transcript, a hover card for one dash showing its prompt, reply, and Jump, Rewind here and Edit & resend buttons, and a bar above the composer reading 'Reading turn 5 of 13'" caption="One dash per turn. Hover one to preview it, and jump to it or rewind there." />

## The timeline rail

With two or more turns, a column of dashes appears on the right edge; one dash per **turn**
(a prompt and everything up to the next prompt).

- Brighter dashes are on screen; **blue** is the turn you're reading (its prompt gets a blue
  bar); **amber** contains a [compaction marker](/features/context-compression).
- **Hover** (or focus) a dash for a preview card; **click** to jump, loading older history if
  needed.

## Rewinding

1. **Pick the point**: from a dash's card, a prompt's hover toolbar (history icon / pencil),
   or **Rewind to here** on the *Reading turn N of M* bar shown while scrolled back (**Latest**
   returns to the bottom).
2. **Check the preview**: a *conversation restarts here* line marks the cut, later turns are
   greyed out and their dashes red. **Rewind** confirms; **Cancel** or <kbd>Esc</kbd> backs out.

<Screenshot src="/screenshots/rewind-preview.png" alt="A rewind preview: a dashed amber 'conversation restarts here' line under turn 6, the turns after it greyed out, their dashes red on the timeline, and an amber bar above the composer reading 'Rewind to turn 6: 7 later turns will be deleted' with Cancel and Rewind buttons" caption="The preview shows exactly what a rewind would delete before you confirm." />

| | Keeps | Deletes | Then |
| --- | --- | --- | --- |
| **Rewind here** | the chosen turn | every later turn | type the next prompt |
| **Edit & resend** | the turns before it | the chosen turn and later | its prompt text is back in the composer (re-attach files yourself) |

- **Undo**: an **8 s** countdown bar brings the turns back (and the composer content for Edit &
  resend). After that, or as soon as you send a prompt or leave, the turns and their
  attachments are deleted for good.
- Rewinding past a compaction marker removes it; the model sees the full history again.
- Not while a reply is streaming (the server answers `409`). Stop or wait first.
- Viewing the timeline never changes anything.

## In agent sessions

An agent's **Activity** tab has the same rail and controls. A turn is one prompt you sent plus
everything the agent did until the next; [goal loop](/features/agents-mode/missions)
continuations stay inside their turn.

<Screenshot src="/screenshots/rewind-agent.png" alt="An agent's Activity tab with a rewind previewed: a dashed 'conversation restarts here' line under the first answer, the later turns greyed out, red dashes on the timeline, and an amber bar above the composer reading 'Rewind to turn 1: 2 later turns will be deleted. This can't be undone.' with Cancel and Rewind buttons, and a note that the dropped turns changed no tracked files" caption="An agent rewind is previewed like a chat's, but it's final once you confirm." />

A rewind cuts the Copilot session's history first, then the transcript. Differences:

- **No undo.** Turns are gone once you confirm.
- **Agent at rest only**: not while a turn runs or waits for approval.
- **Current Copilot session only.** Turns of other sessions (a fresh start, `/clear`) are
  navigation-only. Editing the task starts a new run but keeps the session, so those turns
  stay rewindable.
- **Not during a workflow step**: replay the step from the
  [workflow](/features/workflows) instead.
- **Not past a [compaction](/features/context-compression#compacting-an-agent).**
- **The run is reset** to *idle* with the last kept answer as summary; progress, a raised
  question and the goal loop count are cleared, and
  [artifacts](/features/agents-mode/artifacts-state) published by dropped turns are deleted.

Copies of agent exchanges mirrored into a topic or chat are independent of agent rewinds, and
vice versa.

::: warning Experimental
Agent rewind uses an experimental Copilot SDK API, which may change.
:::

### Restoring files

Tick **Also restore N files** (unticked every time) in the confirmation bar to revert files the
dropped turns created, edited or deleted. **Show files** lists them with lines added/removed.

<Screenshot src="/screenshots/rewind-agent-files.png" alt="An agent rewind preview whose amber confirmation bar has an unticked 'Also restore 2 files these turns changed (+60 −7)' checkbox and an expanded file list: onboarding.md edited +42 −7, checklist.md new +18 −0" caption="Restoring files is opt-in for each rewind, and the list shows what would change." />

- Files return to their state before the first dropped turn. Files **you** changed afterwards
  are left alone; a note says what was restored or skipped and why.
- Restore runs before the conversation is cut; if it fails, the conversation is untouched and
  Precursor says whether every file was put back.
- Requires a session that tracked file changes from its first turn: on by default, controlled
  by **Track file changes for rewind** in
  [Settings → Agents](/guide/configuration#other-settings-areas). Older or remote sessions keep
  their files.

### Runs on the rail

With **All runs** selected, the rail shows every run's turns separated by gaps; the card names
the run (*Run #12 · Schedule*). Rewindable runs are marked *current session*.

## Keyboard

<kbd>Tab</kbd> to the rail, then:

| Key | Action |
| --- | --- |
| <kbd>↑</kbd> / <kbd>↓</kbd>, <kbd>Home</kbd> / <kbd>End</kbd> | Move between turns |
| <kbd>Enter</kbd> | Jump to the turn |
| <kbd>R</kbd> | Preview **Rewind here** |
| <kbd>E</kbd> | Preview **Edit & resend** |
| <kbd>Esc</kbd> | Close the card or cancel a preview |

Screen readers announce each dash as *"Turn N of M"* plus its prompt.

## Over the API

Topics and chats: `GET …/messages/turns`, `POST …/messages/rewind`. Agents:
`GET /api/agents/{id}/rewind/preview`, `POST /api/agents/{id}/rewind`. See the
[API reference](/reference/api).
