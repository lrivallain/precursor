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
// silence) take a few hundred ms to wake, swallowing a short sound. Digital
// silence doesn't wake them, so a near-inaudible noise primer runs first and
// the pop starts once the device is up.
const PRIMER_S = 0.5;
const POP_AT_S = 0.4;
const PRIMER_LEVEL = 0.003;

function primer(c: AudioContext): void {
  const frames = Math.floor(c.sampleRate * PRIMER_S);
  const buf = c.createBuffer(1, frames, c.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < frames; i++) data[i] = (Math.random() * 2 - 1) * PRIMER_LEVEL;
  const src = c.createBufferSource();
  src.buffer = buf;
  src.connect(c.destination);
  src.start();
}

function pop(c: AudioContext): void {
  const t0 = c.currentTime + POP_AT_S;
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = "triangle";
  osc.frequency.value = 440;
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(0.2, t0 + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.15);
  osc.connect(gain).connect(c.destination);
  osc.start(t0);
  osc.stop(t0 + 0.17);
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
        if (c.state !== "running") return;
        primer(c);
        pop(c);
      })
      .catch(() => {});
  } catch {
    // Audio is a nicety; ignore autoplay blocks and unsupported contexts.
  }
}
