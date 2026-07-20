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

Pick the meeting language when creating the session or from the Live toolbar.
Changing it mid-session briefly restarts the recognizer (Azure can't switch a
running recognizer's language in place). Live insights and Q&A answers are
produced in the session's language.

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
  **post into the linked topic** as a message.
- **Context** — an AI summary of the attached topic, and — via the
  [WorkIQ MCP](/features/mcp) (Microsoft 365) — the ability to **link a meeting
  from your agenda** so its invitees flow into the summary's attendees.

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
