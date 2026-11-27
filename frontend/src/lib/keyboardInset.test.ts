import { describe, expect, it } from "vitest";
import { keyboardHeight } from "./keyboardInset";

describe("keyboardHeight", () => {
  it("is the gap between the layout and visual viewports when a keyboard is up", () => {
    expect(keyboardHeight(400, 750, 1)).toBe(350);
  });
  it("ignores small gaps (browser chrome) and no gap", () => {
    expect(keyboardHeight(700, 750, 1)).toBe(0);
    expect(keyboardHeight(750, 750, 1)).toBe(0);
  });
  it("ignores a pinch-zoomed page", () => {
    expect(keyboardHeight(300, 750, 2.5)).toBe(0);
  });
});
