---
title: Live sessions
---

# Live meeting assistant

The **Live** section records an ongoing meeting, transcribes it with speaker
labels, and surfaces live insights, Q&A, and an editable summary you can attach
to a [topic](/features/topics). Audio is transcribed in the browser via **Azure
Speech** and **never stored** — only the transcript and derived insights are kept.

<Screenshot src="/screenshots/live.png" alt="A live session with a speaker-labeled transcript on the left and live insights on the right" caption="A live session — speaker-labeled transcript alongside live insights (action items, decisions, open questions)." />

## Prerequisites

Transcription uses **Azure AI Speech**. Set a Speech **key** and **endpoint**
under **Settings → Speech-to-text**. Until then, sessions can be created but the
**Record** button stays disabled.

## Capturing meeting audio

A browser can only capture audio it's given access to. For an **in-person**
meeting, selecting your microphone is enough. For a **remote** meeting in a
desktop app (like Teams), you route the app's output through a **virtual audio
device** that then looks like a normal microphone to the browser — while still
sending the audio to your speakers so you keep hearing the call.

The **How to capture meeting audio** dialog (the `?` next to the input picker)
has step-by-step, OS-specific instructions:

- **macOS** — BlackHole + a Multi-Output Device.
- **Windows** — VB-CABLE with *Listen to this device* enabled.
- **Linux** — a PipeWire / PulseAudio null sink + loopback.

For **hybrid** meetings, tick **+ mic** to also capture your local microphone
alongside the virtual device — both streams are mixed and transcribed together.

## Starting and protecting a recording

The **record controls** — the **Record** button, capture-device picker, **+ mic**
mix-in, and the meeting **language** — live pinned at the top of the
**Transcript** tab, so they stay in reach even as the transcript grows and
scrolls. (Session-level controls — topic, features, **End session**, archive and
delete — stay in the toolbar above the tabs.)

Connecting to Azure takes a moment — minting a token, loading the Speech SDK, and
opening the capture device — so the **Record** button first shows a transient
**Starting…** state, then flips to a red **Stop** button with a **Recording**
indicator once capture is live. That way a click is never ambiguous.

While a recording is live, Precursor guards against losing it by accident:

- Leaving the screen in-app — switching cockpit, going Home, opening another live
  session, or jumping via search — asks you to confirm first (**Keep recording**
  or **Leave & stop recording**).
- Reloading, closing the tab, or quitting the app triggers the browser's native
  "leave site?" prompt.

## Language

Pick the meeting language when creating the session or from the record controls
at the top of the **Transcript** tab. Changing it mid-session briefly restarts
the recognizer (Azure can't switch a running recognizer's language in place).
Live insights and Q&A answers are produced in the session's language.

## Insights, Q&A, and summary

The Live view is a **tabbed, splittable panel** — Transcript · Live Insights ·
Summary · Context. Use the **Split** toggle to view two sections side by side,
each with its own tab-strip and a draggable divider.

- **Live Insights** — while recording, the assistant re-derives a snapshot of
  **action items, decisions, open questions, suggestions, and risks** from the
  rolling transcript on a short pause / interval cadence.
- **Ask assistant** — ask free-form questions; answers stream from the transcript
  plus the attached topic.
- **Speaker labels** — click a label in the transcript to rename it
  (`Guest-2` → `Thomas`); the name applies to every past and future phrase from
  that voice.
- **Summary** — an editable markdown recap, including an **Attendees** list
  (seeded from renamed speakers and any linked meeting's invitees), which you can
  **post into the linked topic** as a message. When a Teams meeting is linked and
  the [WorkIQ MCP](/features/mcp) is enabled, a **From Teams transcript** button
  builds the recap from the meeting's own published transcript — a **"no local
  record"** path that needs no local recording at all (see below).
- **Context** — an AI summary of the attached topic, and — via the
  [WorkIQ MCP](/features/mcp) (Microsoft 365) — the ability to **link a meeting
  from your agenda** so its invitees flow into the summary's attendees. The
  agenda spans the **last few days through today**, split with a color-coded
  **Past** (amber) vs **Current & upcoming** (emerald) marker that the list
  **auto-scrolls to** (past meetings are capped to the 10 most recent), so you
  can attach — or record from — a meeting that already happened. The same picker
  appears on the **Start a live session** screen.

### Summarize from the Teams transcript (no local record)

If you'd rather not capture audio locally, link the Teams meeting from your
agenda in the **Context** tab (past meetings are listed under the **Past**
marker), then open **Summary → From Teams transcript**. Precursor scrapes the
meeting's published transcript through WorkIQ (Microsoft Graph) and summarizes it
with **your** model — so you get Precursor's structured recap (decisions, action
items, open questions, risks) instead of Teams' own summary.

The button appears **only** when the WorkIQ MCP server is enabled **and** a Teams
meeting is linked. It's best-effort and fail-closed: the transcript is only
available to the meeting **organizer**, requires the delegated
`OnlineMeetingTranscript.Read.All` permission, and is published by Teams a few
minutes **after** the meeting ends (transcription must have been on). When any of
those isn't met, the button reports why and leaves your summary untouched.

## Settings

Configure the section under **Settings → Live**: enable/disable it, and choose
the **fast model + reasoning effort** used for live insights and Q&A (summaries
use your default chat model for quality). Speech credentials live under
**Settings → Speech-to-text**.

## Privacy

Audio is streamed directly from the browser to Azure Speech using a short-lived
token minted by the backend — the subscription key **never reaches the browser**,
and raw audio is **never persisted**. Only the finalized transcript (and derived
insights) are stored.
