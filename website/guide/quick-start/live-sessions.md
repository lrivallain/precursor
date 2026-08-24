---
title: Quick start — Live sessions
---

# Quick start: Live sessions

[Live sessions](/features/live-sessions) record a meeting, transcribe it with
speaker labels, and produce an editable summary you can post into a topic.

## Set Azure Speech credentials first

Transcription uses **Azure AI Speech**. Add a **key** and **endpoint** under
**Settings → Speech-to-text**. Until you do, sessions can be created but the
**Record** button stays disabled.

## Record your first meeting

Open **Live → New session**, optionally attach a [topic](/features/topics) for
context, pick your capture device, and hit **Record**.

For a **remote** meeting you need a virtual audio device so the browser can hear
the call — the `?` next to the input picker has OS-specific instructions
(BlackHole on macOS, VB-CABLE on Windows, a null sink on Linux). Tick **+ mic**
for a hybrid meeting to capture your own voice alongside it.

## While it runs

- **Live Insights** accumulates action items, decisions, open questions and risks
  from the rolling transcript.
- **Ask assistant** answers free-form questions from the transcript plus the
  attached topic.
- Click a **speaker label** to rename it (`Guest-2` → `Thomas`) across every past
  and future phrase, and **double-click any phrase** to fix a misheard word.
- **Summary** gives you a recap — with an attendees list — that you can post
  straight into the linked topic.

Audio is **never stored**: it streams from the browser to Azure with a
short-lived token, and only the transcript and what's derived from it are kept.

Full detail: [Live sessions](/features/live-sessions).
