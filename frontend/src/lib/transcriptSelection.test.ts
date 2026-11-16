import { describe, expect, it } from "vitest";
import {
  clickSelect,
  invertSelection,
  selectFrom,
  shiftIndex,
  shiftMarkers,
} from "./transcriptSelection";

const order = [10, 20, 30, 40, 50];
const sorted = (s: Set<number>) => [...s].sort((a, b) => a - b);

describe("clickSelect", () => {
  it("toggles a single id without an anchor", () => {
    expect(sorted(clickSelect(order, new Set(), 20, null))).toEqual([20]);
    expect(sorted(clickSelect(order, new Set([20]), 20, null))).toEqual([]);
  });

  it("selects the anchor..target range in either direction", () => {
    expect(sorted(clickSelect(order, new Set([20]), 40, 20))).toEqual([20, 30, 40]);
    expect(sorted(clickSelect(order, new Set([40]), 10, 40))).toEqual([10, 20, 30, 40]);
  });

  it("clears the range when the anchor was deselected", () => {
    expect(sorted(clickSelect(order, new Set([10, 20, 40]), 40, 30))).toEqual([10, 20]);
    expect(sorted(clickSelect(order, new Set([20, 30, 40]), 40, 10))).toEqual([]);
  });

  it("falls back to a toggle when the anchor is gone", () => {
    expect(sorted(clickSelect(order, new Set(), 30, 99))).toEqual([30]);
  });
});

describe("selectFrom / invertSelection", () => {
  it("adds the phrase and everything after it", () => {
    expect(sorted(selectFrom(order, new Set([10]), 40))).toEqual([10, 40, 50]);
    expect(sorted(selectFrom(order, new Set(), 99))).toEqual([]);
  });

  it("inverts against the full order", () => {
    expect(sorted(invertSelection(order, new Set([10, 30])))).toEqual([20, 40, 50]);
  });
});

describe("shiftIndex / shiftMarkers", () => {
  it("counts the kept items before an index", () => {
    expect(shiftIndex(order, new Set([20, 30]), 3)).toBe(1);
    expect(shiftIndex(order, new Set(), 3)).toBe(3);
  });

  it("re-points markers and drops ones at the edges or colliding", () => {
    const markers = [
      { index: 1, kind: "resume" },
      { index: 2, kind: "reconnect" },
      { index: 4, kind: "resume" },
    ];
    // Removing 20 and 30 merges the first two markers; 50 is still kept.
    expect(shiftMarkers(order, new Set([20, 30]), markers)).toEqual([
      { index: 1, kind: "resume" },
      { index: 2, kind: "resume" },
    ]);
    // Removing the tail leaves no phrase after the last marker.
    expect(shiftMarkers(order, new Set([50]), markers)).toEqual([
      { index: 1, kind: "resume" },
      { index: 2, kind: "reconnect" },
    ]);
  });
});
