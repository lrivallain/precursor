---
title: Timeline & rewind
---

# Timeline & rewind

Long conversations get a **timeline** that you can skim. When a thread takes a
wrong turn, you can **rewind** it to an earlier point and carry on from there.
This works in [topics](/features/topics) and [chats](/features/chats), and in
[agent sessions](#in-agent-sessions) with a few differences.

<Screenshot src="/screenshots/rewind-timeline.png" alt="A chat scrolled back to an earlier turn, its prompt marked with a short blue bar, with a column of short dashes on the right edge of the transcript, a hover card for one dash showing its prompt, reply, and Jump, Rewind here and Edit & resend buttons, and a bar above the composer reading 'Reading turn 5 of 13'" caption="One dash per turn. Hover one to preview it, and jump to it or rewind there." />

## The timeline rail

A conversation with two or more turns shows a column of short dashes on the right
edge of the transcript. A **turn** is one prompt plus everything up to the next
prompt: the reply, tool calls, and any compaction marker. Each turn gets one dash.

- Dashes for turns **on screen** are brighter. The turn you're **reading** is
  blue: the last one whose prompt has scrolled past the top. At the bottom, it's
  the latest turn. The prompt of that turn gets a short blue bar on its right
  edge, in the same blue, so you can see which message the dash stands for.
- A turn that contains a [compaction marker](/features/context-compression) is
  amber.
- **Hover** a dash, or move to it with the keyboard, for a preview card. It
  shows the turn number, the time, and the start of the prompt and of the reply.
- **Click** a dash to jump to that turn. The rail covers the whole
  conversation, including older turns not yet loaded: jumping to one loads the
  history in between first.

The dashes sit at a fixed spacing, centred on the right edge of the transcript.
In a very long conversation they get closer together so they all still fit.

Looking at the timeline changes nothing. Only an explicit rewind does.

## Rewinding

Rewind is always a two-step action, so you see what will go before anything
changes.

1. **Pick the point.** You can start a rewind from three places:
   - A dash's preview card: **Rewind here** or **Edit & resend**.
   - Any prompt's hover toolbar: the history icon for **Rewind here**, the
     pencil for **Edit & resend**. While you're scrolled back, the toolbar of
     the prompt with the blue bar stays visible without hovering.
   - The **Reading turn N of M** bar above the composer, shown while you're
     scrolled back: **Rewind to here**. That bar also has a **Latest** button
     that takes you back to the bottom.
2. **Check the preview.** A dashed *conversation restarts here* line marks the
   cut. The turns after it are greyed out and their dashes turn red. The bar
   above the composer says how many turns will be deleted. Choose **Rewind** to
   go ahead, or **Cancel** (or press <kbd>Esc</kbd>) to back out.

<Screenshot src="/screenshots/rewind-preview.png" alt="A rewind preview: a dashed amber 'conversation restarts here' line under turn 6, the turns after it greyed out, their dashes red on the timeline, and an amber bar above the composer reading 'Rewind to turn 6: 7 later turns will be deleted' with Cancel and Rewind buttons" caption="The preview shows exactly what a rewind would delete before you confirm." />

There are two ways to rewind:

| | Keeps | Deletes | Then |
| --- | --- | --- | --- |
| **Rewind here** | the chosen turn, prompt and reply | every later turn | type the next prompt as usual |
| **Edit & resend** | the turns before it | the chosen turn and every later one | its prompt is back in the composer, ready to edit and send |

**Edit & resend** puts only the prompt's text back in the composer. Attach any
files again yourself.

### Undo

After you confirm, the turns disappear right away. An undo bar counts down for
**8 seconds**, for example *"Rewound · 7 turns removed · undo in 8s"*. **Undo**
brings the turns back. For **Edit & resend**, it also restores what was in the
composer, unless you've already started editing the prompt.

Once the countdown ends, the turns are **deleted** for good, along with their
attachments, just like [deleting a message](/features/topics#deleting-clearing-and-stopping).
Precursor deletes them straight away if you send a new prompt or leave the
conversation, so a new turn never ends up inside a cut.

Rewinding past a [compaction marker](/features/context-compression) removes the
marker too. The model then works from the full history before the cut again.

### While a reply is streaming

You can't rewind while a reply is being generated. The buttons are disabled, and
the server refuses with `409` so the reply can't land after a cut. Stop the
reply or wait for it to finish first.

## In agent sessions

An agent's **Activity** tab has the same rail, preview card, prompt toolbar and
*Reading turn N of M* bar. A turn is one prompt you sent, plus everything the
agent did up to your next prompt. The *continued autonomously* prompts that the
[goal loop](/features/agents-mode/missions) sends stay inside the turn they
continue: they don't get dashes of their own.

<Screenshot src="/screenshots/rewind-agent.png" alt="An agent's Activity tab with a rewind previewed: a dashed 'conversation restarts here' line under the first answer, the later turns greyed out, red dashes on the timeline, and an amber bar above the composer reading 'Rewind to turn 1: 2 later turns will be deleted. This can't be undone.' with Cancel and Rewind buttons, and a note that the dropped turns changed no tracked files" caption="An agent rewind is previewed like a chat's, but it's final once you confirm." />

The conversation an agent works from lives in its Copilot session, not in
Precursor's transcript. So a rewind cuts **both**: the session's history first,
so the model no longer sees the dropped turns, then the transcript. That changes
a few things:

- **No undo.** The Copilot session can't bring dropped turns back. The preview
  says *This can't be undone*, and the turns are gone as soon as you confirm.
- **Only when the agent is at rest.** Rewind is unavailable while a turn is
  running or waiting for an approval. Stop the agent, or let it finish, first.
- **Only the current session.** A new [run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)
  usually starts a new Copilot session, for example a fresh start or `/clear`.
  So only the turns of the session the agent is in now can be rewound: the
  current run's, and those of an earlier run that carried on the same session
  (editing the agent's task restarts it on a new run but keeps the
  conversation). Turns from other sessions, and turns recorded before rewind
  existed, stay on the rail for navigation only.
- **Not on a workflow step.** While the agent's current run is driven by a
  [workflow](/features/workflows) step, rewind is unavailable: the workflow
  has already passed that step's output on. Replay the step from the workflow
  instead.
- **Not past a compaction.** Once a [compaction](/features/context-compression#compacting-an-agent)
  has folded turns into a summary, the session can't return to them. Precursor
  says so instead of rewinding.
- **The run is reset.** The agent rests as *idle*, with the last kept answer as
  its summary. Progress, a raised question and the goal loop's step count are
  cleared, and [artifacts](/features/agents-mode/artifacts-state) the run
  published from the dropped turns on are deleted.

An agent exchange mirrored into a topic or chat is a separate copy. Rewinding
the agent doesn't touch it, and rewinding the topic or chat doesn't touch the
agent.

::: warning Experimental
Agent rewind uses an experimental Copilot SDK API, which may change.
:::

### Restoring files

A rewind can also put back the files the dropped turns changed. When you start
one, Precursor asks the agent's Copilot session which files those turns
created, edited or deleted, and the confirmation bar offers **Also restore N
files** with the total lines added and removed. **Show files** lists them. The
box is unticked every time: leave it, and only the conversation is rewound.

<Screenshot src="/screenshots/rewind-agent-files.png" alt="An agent rewind preview whose amber confirmation bar has an unticked 'Also restore 2 files these turns changed (+60 −7)' checkbox and an expanded file list: onboarding.md edited +42 −7, checklist.md new +18 −0" caption="Restoring files is opt-in for each rewind, and the list shows what would change." />

- Files go back to how they were before the first dropped turn.
- A file **you** changed after the agent did is left alone. After the rewind,
  a note says how many files were restored and how many were left as they were.
  Hover it to see which ones, and why.
- File restore happens before the conversation is cut. If it fails, the
  conversation is left as it was and Precursor says whether every file was put
  back. If some couldn't be, check the agent's files before you try again.
- It needs a session that tracked its file changes from its first turn. New
  sessions do, unless you turn off **Track file changes for rewind** in
  [Settings → Agents](/guide/configuration#other-settings-areas). For an older session, or a
  remote one, the bar says the files will stay as they are.

### Runs on the rail

With **All runs** selected, the rail shows every run's turns. A short gap
separates one run from the next, and the preview card names the run, for example
*Run #12 · Schedule*. The runs you can rewind are marked *current session*.

## Keyboard

Press <kbd>Tab</kbd> to reach the rail. Then:

| Key | Action |
| --- | --- |
| <kbd>↑</kbd> / <kbd>↓</kbd>, <kbd>Home</kbd> / <kbd>End</kbd> | Move between turns. The preview card follows the focus. |
| <kbd>Enter</kbd> | Jump to the turn. |
| <kbd>R</kbd> | Preview **Rewind here** on the turn. |
| <kbd>E</kbd> | Preview **Edit & resend** on the turn. |
| <kbd>Esc</kbd> | Close the card, or cancel a preview. |

Screen readers hear each dash as *"Turn N of M"* followed by its prompt. The
current turn is announced as the current step, and the preview, removal and undo
are announced as status updates.

## Over the API

The rail and rewind use two endpoints, available on both topics and chats:
`GET …/messages/turns` and `POST …/messages/rewind`. Agents use
`GET /api/agents/{id}/rewind/preview` and `POST /api/agents/{id}/rewind`, and
build their turns from the transcript. See
the [API reference](/reference/api).
