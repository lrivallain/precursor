---
title: Quick start — Live sessions
---

# Quick start: Live sessions

[Live sessions](/features/live-sessions) transcribe a meeting with speaker labels
and produce an editable summary.

## Set up Azure Speech

In **Settings → Speech-to-text**, set the Azure AI Speech **endpoint** and a
**subscription key** or **Azure CLI** sign-in
([details](/guide/configuration#speech-to-text-live-sessions)). Until then,
**Record** is disabled.

## Record

**Live → New session**, optionally attach a [topic](/features/topics) for
context, pick the input device, **Record**.

For a remote meeting, route the call audio through a virtual device (BlackHole
on macOS, VB-CABLE on Windows, a null sink on Linux; the `?` next to the input
picker explains). Tick **+ mic** to add your own voice.

## While it runs

- **Live Insights**: action items, decisions, open questions, risks.
- **Ask assistant**: questions over the transcript and the attached topic.
- Click a **speaker label** to rename it everywhere; **double-click a phrase** to
  correct it.
- **Summary**: a recap with attendees, postable to the linked topic.

Audio goes from the browser to Azure with a short-lived token and is never stored.
