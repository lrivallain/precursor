---
title: Live sessions
---

# Live meeting assistant

The **Live** section transcribes a meeting with speaker labels and gives you live insights,
Q&A, suggested answers and an editable recap you can post to a [topic](/features/topics).
Audio is transcribed via **Azure Speech** and **never stored**.

<Screenshot src="/screenshots/live.png" alt="A live session with a speaker-labeled transcript on the left and grouped live insights plus a structured suggestion on the right" caption="Live insights beside the transcript: a concise recommendation, grouped follow-ups, decisions, questions and risks." />

## Setup

1. **Settings → Speech-to-text**: set the Azure Speech **endpoint**, then a **key** or Azure
   CLI sign-in (`az login`, optional tenant). See
   [Speech-to-text configuration](/guide/configuration#speech-to-text-live-sessions). Until
   then **Record** stays disabled.
2. **Settings → Live**: enable the section, pick the **fast model + effort** for insights, Q&A
   and suggested answers (recaps use your default chat model), and the
   [transcript retention](#transcript-retention).

## Capturing meeting audio

The browser records any input device. For an in-person meeting, pick your microphone. For a
remote meeting in a desktop app (Teams), route its output through a **virtual audio device**
while still sending it to your speakers. The `?` next to the input picker has per-OS steps:

| OS | Setup |
| --- | --- |
| macOS | BlackHole + a Multi-Output Device |
| Windows | VB-CABLE with *Listen to this device* |
| Linux | PipeWire / PulseAudio null sink + loopback |

Tick **+ mic** to mix your microphone in (hybrid meetings). The device is only open while
recording.

## Recording

The record controls (**Record**, device, **+ mic**, **language**) are pinned at the top of the
**Transcript** tab. While recording, leaving the screen asks for confirmation and closing the
tab triggers the browser's leave prompt.

Changing the **language** mid-session briefly restarts the recognizer. Insights and answers
use the session's language.

In the transcript:

- Click a **speaker label** to rename it (`Guest-2` → `Thomas`) everywhere, or mark it **This
  is me**.
- **Double-click a phrase** to fix a misheard word.
- **Discard phrases in bulk** (e.g. a recording left running after the meeting): **Select**
  above the transcript, tick phrases (**Shift-click** for a range, the arrow next to a checkbox
  for "from here to the end", **All** / **Invert** / **Clear**), then **Discard**. It is
  permanent; re-run the analysis if the insights already used the discarded talk.

## The panels

The view is a set of tabs; **Split** shows two side by side. **Transcript**, **Summary** and
**Context** are always there; the session's **feature picker** adds the others. The **Role**
picker in the header sets the persona for every AI feature of the session. Attaching a topic
copies the topic's role; you can override it afterwards.

| Tab | What it does |
| --- | --- |
| **Live insights** (feature) | Re-derives action items, decisions, open questions, suggestions and risks from the rolling transcript. Each pass replaces the previous snapshot. |
| **Assistant** (feature) | A chat grounded in the transcript and the attached topic. Also opened by [Suggested answers](#suggested-answers). |
| **Translation** (feature) | Live translation of the transcript into a language you pick. |
| **Notes** (feature) | Your Markdown scratch pad. |
| **Summary** | Editable Markdown recap with **Attendees** (from renamed speakers and linked-meeting invitees). Autosaves; **post into the linked topic** as a message. |
| **Context** | AI summary of the attached topic; **link a meeting** from your agenda (last few days through today) via the [WorkIQ MCP](/features/mcp). |

::: tip Without Microsoft 365
With the WorkIQ server off, there is no meeting picker and no Teams transcript source, and
Precursor never calls WorkIQ. Start sessions by hand and summarize the local recording.
:::

### Suggested answers

Turn on **Suggested answers** in the feature picker (per session; it also starts the
Assistant chat). When someone else asks a question (a trailing `?` or an English/French
interrogative opener, joined across one speaker's phrases), a card streams a short
first-person answer within about a second. **Copy** it, **Dig deeper** in the Assistant chat,
or dismiss it.

- Uses the [fast model](#setup) with a short transcript tail, latest insights, pinned notes,
  the attached topic and the role. No tools.
- Phrases from a **This is me** speaker never trigger it, and your speaking cancels a pending
  one. Mark yourself again after a resume (labels restart).
- Finished answers are saved in the Assistant chat as *Question heard* + answer.
- One question at a time, each asked once, with a cooldown and per-minute cap.

### Generating the summary

**Generate** (**Regenerate** once there's a recap) opens a form:

<Screenshot src="/screenshots/live-summary-templates.png" alt="The Generate panel under the Summary tab's button: a Recording / Teams transcript source choice, the list of summary templates including a custom one marked as a file, and the language picker" caption="One button: pick the source, the template — built in, or one of your own definition files — and the language." />

- **Source**: the **Recording**, or the linked meeting's
  [Teams transcript](#summarize-from-the-teams-transcript-no-local-record). Unavailable
  options are greyed out with the reason on hover.
- **Template**:

  | Template | Output |
  | --- | --- |
  | **Meeting recap** (default) | Summary, attendees, decisions, action items, open questions, risks. |
  | **Executive brief** | Short, outcome-first brief. |
  | **Action items** | Table of tasks, owners, due dates. |
  | **Detailed minutes** | Topic-by-topic minutes in order. |
  | **Follow-up email** | Ready-to-send recap email. |

- **Language**: the meeting language (default) or any other.

Template and language are remembered for the next recap and session. Regenerating replaces the
current recap.

#### Your own templates

Templates are [definition files](/features/definitions#summary-templates)
(`summaries/*.summary.yaml` in the **Definitions** workspace).

- **Edit** opens the selected template in [Files](/features/workspaces). For a built-in it
  first saves a copy with the same id, which overrides the built-in until deleted.
- **New from this one** saves a copy under a new id and opens it.

A file with errors is left out of the list (the form says why); the built-in of that id stays
available.

```yaml
# summaries/customer-call.summary.yaml
kind: summary
id: customer-call
name: Customer call notes
description: What the customer needs, what each side committed to, and what's next.
prompt: |
  You are an account manager writing up a call with a customer. …
  ## Needs & pain points — bullet list, in the customer's own terms.
  ## Commitments — two bullet lists, **Us** and **Customer**, with owners.
  ## Next steps — bullet list.
```

### Summarize from the Teams transcript (no local record)

Link the Teams meeting in **Context**, then **Summary → Generate** with the **Teams
transcript** source. Precursor reads the published transcript via WorkIQ (Microsoft Graph) and
summarizes it with your model.

Requirements (otherwise generation explains why and leaves the summary untouched):

- WorkIQ MCP server enabled and a Teams meeting linked.
- You are the meeting **organizer**, with delegated `OnlineMeetingTranscript.Read.All`.
- Teams has published the transcript (a few minutes after the meeting ends).

Teams creates a new transcript each time transcription restarts. When there are several,
Precursor lists them with start/end times; tick the ones to stitch together in chronological
order. Sessions inside the linked meeting's slot are pre-ticked (*matches this meeting*);
recurring meetings can list other occurrences.

## Transcript retention

A session's raw transcript is deleted **7 days** (default) after the session ends. Set
**Settings → Live → Delete transcript after (days)** to `0` to keep it forever
(`live_transcript_retention_days`, see the
[configuration reference](/reference/configuration#retention)). Insights, notes and summary are
kept; active or paused sessions are never touched. The
[storage cleanup cockpit](/features/storage#the-cleanup-cockpit) runs the sweep on demand.

## Privacy

The browser streams audio straight to Azure Speech with a short-lived token minted by the
backend; the key or Azure CLI credential never reaches the browser. Raw audio is never
persisted; only the transcript and derived insights are stored.
