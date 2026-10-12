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

export function playCompletionSound(): void {
  try {
    const c = context();
    if (!c) return;
    void c.resume();
    const t0 = c.currentTime;
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = "triangle";
    osc.frequency.value = 440;
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.15, t0 + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.09);
    osc.connect(gain).connect(c.destination);
    osc.start(t0);
    osc.stop(t0 + 0.11);
  } catch {
    // Audio is a nicety; ignore autoplay blocks and unsupported contexts.
  }
}
