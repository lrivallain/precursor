import { useMemo, useState } from "react";
import { X } from "lucide-react";

type OS = "macos" | "windows" | "linux";

function detectOS(): OS {
  if (typeof navigator === "undefined") return "macos";
  const p = `${navigator.platform} ${navigator.userAgent}`.toLowerCase();
  if (p.includes("win")) return "windows";
  if (p.includes("linux") || p.includes("android")) return "linux";
  return "macos";
}

const TABS: { os: OS; label: string }[] = [
  { os: "macos", label: "macOS" },
  { os: "windows", label: "Windows" },
  { os: "linux", label: "Linux" },
];

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface text-[11px] font-medium text-muted">
        {n}
      </span>
      <span className="flex-1">{children}</span>
    </li>
  );
}

function MacInstructions() {
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted">
        macOS can&apos;t share another app&apos;s audio with the browser directly,
        so route it through a free virtual device (<strong>BlackHole</strong>) and
        a Multi-Output Device so you still hear the call.
      </p>
      <ol className="space-y-2">
        <Step n={1}>
          Install BlackHole (2ch):{" "}
          <code className="rounded bg-surface px-1">brew install blackhole-2ch</code>{" "}
          or download it from{" "}
          <a
            href="https://existential.audio/blackhole/"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            existential.audio/blackhole
          </a>
          . Reboot if prompted.
        </Step>
        <Step n={2}>
          Open <strong>Audio MIDI Setup</strong> → the <strong>+</strong> button →{" "}
          <strong>Create Multi-Output Device</strong>. Tick both your headphones
          and <strong>BlackHole 2ch</strong> so audio plays to both.
        </Step>
        <Step n={3}>
          In <strong>System Settings → Sound → Output</strong>, select that
          Multi-Output Device (you keep hearing the meeting through your
          headphones).
        </Step>
        <Step n={4}>
          Point Teams&apos; speaker output at the Multi-Output Device (or leave it
          on system default), then pick <strong>BlackHole 2ch</strong> as the
          input device below.
        </Step>
      </ol>
    </div>
  );
}

function WindowsInstructions() {
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted">
        Route Teams&apos; output through a virtual cable and monitor it back to
        your speakers so you still hear the call.
      </p>
      <ol className="space-y-2">
        <Step n={1}>
          Install <strong>VB-CABLE</strong> from{" "}
          <a
            href="https://vb-audio.com/Cable/"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            vb-audio.com/Cable
          </a>{" "}
          (run the installer as administrator, then reboot). VoiceMeeter works too
          if you want finer mixing.
        </Step>
        <Step n={2}>
          In <strong>Sound settings → Playback</strong>, set <strong>CABLE Input</strong>{" "}
          as the default output — or set it only for Teams via{" "}
          <strong>App volume &amp; device preferences</strong>.
        </Step>
        <Step n={3}>
          To keep hearing the call, open <strong>CABLE Output</strong> properties →{" "}
          <strong>Listen</strong> tab → tick <em>Listen to this device</em> and
          choose your headphones.
        </Step>
        <Step n={4}>
          Pick <strong>CABLE Output</strong> as the input device below.
        </Step>
      </ol>
      <p className="text-[12px] text-muted">
        Alternatively on Windows you can skip the virtual cable and pick a browser
        &ldquo;share system audio&rdquo; source — but the loopback device is more
        reliable for the desktop Teams app.
      </p>
    </div>
  );
}

function LinuxInstructions() {
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted">
        On PipeWire/PulseAudio, create a null sink and a loopback so Teams plays
        to both your speakers and a capturable monitor.
      </p>
      <ol className="space-y-2">
        <Step n={1}>
          Create a virtual sink:{" "}
          <code className="rounded bg-surface px-1">
            pactl load-module module-null-sink sink_name=meeting
            sink_properties=device.description=Meeting
          </code>
        </Step>
        <Step n={2}>
          Loop it back to your real output so you still hear it:{" "}
          <code className="rounded bg-surface px-1">
            pactl load-module module-loopback source=meeting.monitor
          </code>
        </Step>
        <Step n={3}>
          In <strong>pavucontrol → Playback</strong>, move Teams&apos; stream to the{" "}
          <strong>Meeting</strong> sink.
        </Step>
        <Step n={4}>
          Pick <strong>Monitor of Meeting</strong> as the input device below.
        </Step>
      </ol>
    </div>
  );
}

/**
 * Modal explaining how to install a virtual audio device so the meeting app's
 * audio (Teams, etc.) can be captured by the browser for transcription.
 */
export function LiveAudioHelp({ onClose }: { onClose: () => void }) {
  const [os, setOs] = useState<OS>(useMemo(detectOS, []));

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[85vh] w-[min(640px,100%)] flex-col overflow-hidden rounded-lg border border-border bg-bg shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">Capture your meeting audio</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-muted hover:bg-surface"
          >
            <X size={16} />
          </button>
        </div>

        <div className="border-b border-border px-4 pt-3">
          <div className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.os}
                type="button"
                onClick={() => setOs(t.os)}
                className={`rounded-t px-3 py-1.5 text-sm ${
                  os === t.os
                    ? "border-b-2 border-accent text-accent"
                    : "text-muted hover:bg-surface"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-y-auto px-4 py-4">
          {os === "macos" && <MacInstructions />}
          {os === "windows" && <WindowsInstructions />}
          {os === "linux" && <LinuxInstructions />}
          <p className="mt-4 border-t border-border pt-3 text-[12px] text-muted">
            Once installed, the virtual device appears as a microphone in the
            picker below. Nothing is stored beyond the transcript — audio is
            streamed to the speech service and never saved.
          </p>
        </div>

        <div className="flex justify-end border-t border-border px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded bg-accent px-3 py-1.5 text-sm text-white"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
