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

<Screenshot src="/screenshots/rewind-agent.png" alt="An agent's Activity tab with a rewind previewed: a dashed 'conversation restarts here' line under the first answer, the later turns greyed out, red dashes on the timeline, and an amber bar above the composer reading 'Rewind to turn 1: 2 later turns will be deleted. This can't be undone.' with Cancel and Rewind buttons" caption="An agent rewind is previewed like a chat's, but it's final once you confirm." />

The conversation an agent works from lives in its Copilot session, not in
Precursor's transcript. So a rewind cuts **both**: the session's history first,
so the model no longer sees the dropped turns, then the transcript. That changes
a few things:

- **No undo.** The Copilot session can't bring dropped turns back. The preview
  says *This can't be undone*, and the turns are gone as soon as you confirm.
- **Only when the agent is at rest.** Rewind is unavailable while a turn is
  running or waiting for an approval. Stop the agent, or let it finish, first.
- **Only the current run.** Each [run](/features/agents-mode/orchestration#an-agent-is-a-definition-each-start-is-a-run)
  (for example after `/clear`) is a new Copilot session, so only the latest
  run's turns can be rewound. Turns from earlier runs, and turns recorded before
  rewind existed, stay on the rail for navigation only.
- **Not past a compaction.** Once a [compaction](/features/context-compression#compacting-an-agent)
  has folded turns into a summary, the session can't return to them. Precursor
  says so instead of rewinding.
- **The run is reset.** The agent rests as *idle*, with the last kept answer as
  its summary. Progress, a raised question and the goal loop's step count are
  cleared, and [artifacts](/features/agents-mode/artifacts-state) the run
  published from the dropped turns on are deleted.
- **Files aren't restored.** A rewind only cuts the conversation. Changes the
  agent made to files stay as they are.

An agent exchange mirrored into a topic or chat is a separate copy. Rewinding
the agent doesn't touch it, and rewinding the topic or chat doesn't touch the
agent.

::: warning Experimental
Agent rewind uses an experimental Copilot SDK API, which may change.
:::

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
`POST /api/agents/{id}/rewind`, and build their turns from the transcript. See
the [API reference](/reference/api).
