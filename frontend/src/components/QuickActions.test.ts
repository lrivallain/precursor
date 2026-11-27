import { describe, expect, it } from "vitest";
import { dragThreshold, pillClipped } from "./QuickActions";

describe("dragThreshold", () => {
  it("lets a fingertip wobble more than a mouse before a tap becomes a drag", () => {
    expect(dragThreshold("touch")).toBeGreaterThan(dragThreshold("mouse"));
    expect(dragThreshold("pen")).toBe(dragThreshold("touch"));
    expect(dragThreshold("mouse")).toBe(6);
  });
});

describe("pillClipped", () => {
  // A 44px tile in a 120px tall scrolling menu (a short phone screen).
  it("keeps the label of a tile that is mostly in view", () => {
    expect(pillClipped(0, 44, 120)).toBe(false);
    expect(pillClipped(-20, 44, 120)).toBe(false);
    expect(pillClipped(90, 44, 120)).toBe(false);
  });
  it("hides the label of a tile scrolled mostly out of view", () => {
    expect(pillClipped(-30, 44, 120)).toBe(true);
    expect(pillClipped(110, 44, 120)).toBe(true);
    expect(pillClipped(400, 44, 120)).toBe(true);
  });
});
