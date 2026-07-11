# Live meeting assistant

The **Live** section (`/live`) records an ongoing meeting, transcribes it with
speaker labels, and — in later phases — surfaces live insights and can attach a
summary to a topic. Audio is transcribed in the browser via Azure Speech and
**never stored**; only the transcript and derived insights are kept.

## Prerequisites

Transcription uses **Azure AI Speech**. Set a Speech **key** and **endpoint** in
*Settings → Speech*. Until then, sessions can be created but the **Record**
button stays disabled.

## Capturing meeting audio

A browser can only capture audio it is given access to. For an **in-person**
meeting, selecting your microphone is enough. For a **remote** meeting in the
desktop Teams app (which is not a browser tab), you route the app's output
through a **virtual audio device** that then appears to the browser as a normal
microphone. You keep hearing the call by also sending the audio to your
speakers/headset.

Open the **How to capture meeting audio** dialog (the `?` next to the input
picker) for step-by-step, OS-specific instructions. In short:

### macOS — BlackHole + Multi-Output Device

1. Install BlackHole: `brew install blackhole-2ch` (or from
   [existential.audio/blackhole](https://existential.audio/blackhole/)).
2. In **Audio MIDI Setup**, create a **Multi-Output Device** combining your
   headphones and **BlackHole 2ch**.
3. Set that Multi-Output Device as the system output.
4. Choose **BlackHole 2ch** as the input device in the Live toolbar.

### Windows — VB-CABLE

1. Install [VB-CABLE](https://vb-audio.com/Cable/) (as administrator) and reboot.
2. Set **CABLE Input** as the output for Teams (or system-wide).
3. In **CABLE Output → Properties → Listen**, enable *Listen to this device* and
   pick your headphones so you still hear the call.
4. Choose **CABLE Output** as the input device in the Live toolbar.

### Linux — PipeWire / PulseAudio null sink

1. `pactl load-module module-null-sink sink_name=meeting sink_properties=device.description=Meeting`
2. `pactl load-module module-loopback source=meeting.monitor`
3. In `pavucontrol`, move Teams' playback stream to the **Meeting** sink.
4. Choose **Monitor of Meeting** as the input device in the Live toolbar.

### Hybrid meetings

For a mix of in-room and remote participants, tick **+ mic** to also capture
your local microphone alongside the virtual device — both streams are mixed and
transcribed together.

## Language

Pick the meeting language when creating the session or from the Live toolbar.
Changing it mid-session briefly restarts the recognizer (Azure can't switch a
running recognizer's language in place).

## Privacy

Audio is streamed directly from the browser to Azure Speech using a short-lived
token minted by the backend — the subscription key never reaches the browser,
and raw audio is never persisted. Only the finalized transcript (and later,
derived insights) are stored.
