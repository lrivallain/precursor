import { describe, expect, it } from "vitest";
import { wakeupCountdown, wakeupHint, type Wakeup } from "./wakeupStore";

const NOW = Date.parse("2026-11-25T12:00:00Z");
const at = (seconds: number) => new Date(NOW + seconds * 1000).toISOString();

describe("wakeupCountdown", () => {
  it("reads as a short relative delay", () => {
    expect(wakeupCountdown(at(-5), NOW)).toBe("now");
    expect(wakeupCountdown(at(30), NOW)).toBe("in 30 s");
    expect(wakeupCountdown(at(12 * 60), NOW)).toBe("in 12 min");
    expect(wakeupCountdown(at(3 * 3600), NOW)).toBe("in 3 h");
    expect(wakeupCountdown(at(5 * 86400), NOW)).toBe("in 5 days");
  });
});

describe("wakeupHint", () => {
  const wakeup: Wakeup = {
    id: 1,
    kind: "topic",
    target_id: 2,
    title: "T",
    wake_at: at(600),
    prompt: "Check the CI",
    reason: null,
    created_at: at(0),
  };

  it("names when it wakes up, and what it waits for when known", () => {
    expect(wakeupHint(wakeup, NOW)).toMatch(/^Paused: wakes up in 10 min \(/);
    expect(wakeupHint({ ...wakeup, reason: "Waiting for the build" }, NOW)).toMatch(
      /\nWaiting for the build$/,
    );
  });
});
