import { describe, expect, it } from "vitest";
import { startSwipe, swipeOf, swipeScrollTop, SWIPE_THRESHOLD } from "./labelSwipe";

describe("label swipe", () => {
  it("stays a tap while the finger barely moves", () => {
    const swipe = startSwipe(1, 50, 200, 40);
    expect(swipeScrollTop(swipe, 52, 200 + SWIPE_THRESHOLD - 3)).toBeNull();
    expect(swipe.moved).toBe(false);
  });

  it("scrolls the menu with the finger once it is a swipe", () => {
    const swipe = startSwipe(1, 50, 200, 40);
    expect(swipeScrollTop(swipe, 50, 170)).toBe(70); // finger up: the list scrolls down
    expect(swipe.moved).toBe(true);
    expect(swipeScrollTop(swipe, 50, 198)).toBe(42); // back within the threshold: still a swipe
    expect(swipeScrollTop(swipe, 50, 300)).toBe(0); // never above the top
  });

  it("treats a sideways swipe as a swipe, not a tap, without scrolling", () => {
    const swipe = startSwipe(1, 50, 200, 40);
    expect(swipeScrollTop(swipe, 90, 201)).toBe(39);
    expect(swipe.moved).toBe(true);
  });
});

describe("swipeOf", () => {
  it("ignores every pointer but the one that started the swipe", () => {
    const swipe = startSwipe(7, 50, 200, 40);
    expect(swipeOf(swipe, 7)).toBe(swipe);
    expect(swipeOf(swipe, 8)).toBeNull(); // a second finger, or a mouse
    expect(swipeOf(null, 7)).toBeNull();
  });
});
