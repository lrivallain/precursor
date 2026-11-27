import { describe, expect, it } from "vitest";
import { dragThreshold } from "./QuickActions";

describe("dragThreshold", () => {
  it("lets a fingertip wobble more than a mouse before a tap becomes a drag", () => {
    expect(dragThreshold("touch")).toBeGreaterThan(dragThreshold("mouse"));
    expect(dragThreshold("pen")).toBe(dragThreshold("touch"));
    expect(dragThreshold("mouse")).toBe(6);
  });
});
