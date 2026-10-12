/**
 * Completion sound: a short, quiet "pop" synthesized with Web Audio, so there
 * is no asset to ship. Opt-in via Settings -> Chat. Best-effort: browsers keep
 * an AudioContext suspended until a user gesture, and a blocked or missing
 * context must never surface as an error.
 */

let ctx: AudioContext | null = null;

function context(): AudioContext | null {
  if (typeof window === "undefined" || !("AudioContext" in window)) return null;
  ctx ??= new AudioContext();
  return ctx;
}

// Output devices (Bluetooth speakers, HDMI, a Chrome stream idled after long
// silence) can take ~100 ms to wake, swallowing the start of a short sound.
// Scheduling the pop after that lead-in keeps it audible.
const WAKE_LEAD_S = 0.2;

function pop(c: AudioContext): void {
  const t0 = c.currentTime + WAKE_LEAD_S;
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = "triangle";
  osc.frequency.value = 440;
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(0.2, t0 + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.12);
  osc.connect(gain).connect(c.destination);
  osc.start(t0);
  osc.stop(t0 + 0.14);
}

export function playCompletionSound(): void {
  try {
    const c = context();
    if (!c) return;
    // Wait for the context to actually be running: scheduling on a suspended
    // one plays into the void when the autoplay policy hasn't lifted yet.
    void c
      .resume()
      .then(() => {
        if (c.state === "running") pop(c);
      })
      .catch(() => {});
  } catch {
    // Audio is a nicety; ignore autoplay blocks and unsupported contexts.
  }
}
