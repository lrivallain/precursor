import { describe, expect, it } from "vitest";
import { ancestorDirs, revealPath } from "./fileTreePaths";

describe("ancestorDirs", () => {
  it("lists the containing folders, outermost first", () => {
    expect(ancestorDirs("a/b/c.md")).toEqual(["a", "a/b"]);
  });

  it("is empty for a file at the root", () => {
    expect(ancestorDirs("c.md")).toEqual([]);
  });
});

describe("revealPath", () => {
  it("opens every collapsed ancestor and keeps other folders", () => {
    const collapsed = new Set(["a", "a/b", "x"]);
    expect([...revealPath(collapsed, "a/b/c.md")]).toEqual(["x"]);
  });

  it("returns the same set when the path is already visible", () => {
    const collapsed = new Set(["x"]);
    expect(revealPath(collapsed, "a/b/c.md")).toBe(collapsed);
  });
});
