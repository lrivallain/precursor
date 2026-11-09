import { describe, expect, it } from "vitest";
import { lineChanges } from "./diffGutter";

describe("lineChanges", () => {
  it("marks pure additions as added", () => {
    const diff = ["@@ -1,2 +1,4 @@", " a", "+b", "+c", " d"].join("\n");
    expect(lineChanges(diff)).toEqual([{ kind: "added", start: 2, end: 3 }]);
  });

  it("marks replaced lines as modified", () => {
    const diff = ["@@ -1,3 +1,3 @@", " a", "-b", "+B", " c"].join("\n");
    expect(lineChanges(diff)).toEqual([{ kind: "modified", start: 2, end: 2 }]);
  });

  it("marks removed lines as a deletion after the previous line", () => {
    const diff = ["@@ -1,3 +1,2 @@", " a", "-b", " c"].join("\n");
    expect(lineChanges(diff)).toEqual([{ kind: "deleted", start: 1, end: 1 }]);
  });

  it("returns nothing for an empty diff", () => {
    expect(lineChanges("")).toEqual([]);
  });
});
