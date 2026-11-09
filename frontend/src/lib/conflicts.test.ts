import { describe, expect, it } from "vitest";
import { findConflicts, resolution } from "./conflicts";

const lines = [
  "intro",
  "<<<<<<< HEAD",
  "ours 1",
  "ours 2",
  "=======",
  "theirs",
  ">>>>>>> origin/main",
  "outro",
];

describe("findConflicts", () => {
  it("finds a block with its markers and labels", () => {
    expect(findConflicts(lines)).toEqual([
      { start: 2, middle: 5, end: 7, oursLabel: "HEAD", theirsLabel: "origin/main" },
    ]);
  });

  it("ignores an unterminated block", () => {
    expect(findConflicts(["<<<<<<< HEAD", "x", "======="])).toEqual([]);
  });
});

describe("resolution", () => {
  const [block] = findConflicts(lines);

  it.each([
    ["current", ["ours 1", "ours 2"]],
    ["incoming", ["theirs"]],
    ["both", ["ours 1", "ours 2", "theirs"]],
  ] as const)("keeps %s", (choice, expected) => {
    expect(resolution(lines, block, choice)).toEqual(expected);
  });
});
