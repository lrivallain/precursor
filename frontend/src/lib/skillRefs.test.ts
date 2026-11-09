import { describe, expect, it } from "vitest";
import { expandSkillReferences } from "./skillRefs";

const skills = new Map([
  ["rewrite", "Rewrite the text clearly."],
  ["polite", "Be polite. /rewrite"],
  ["loop", "Again /loop"],
]);

describe("expandSkillReferences", () => {
  it("substitutes a reference in the middle of prose", () => {
    expect(expandSkillReferences("tidy this with /rewrite please", skills)).toBe(
      "tidy this with Rewrite the text clearly. please",
    );
  });

  it("leaves paths, URLs and unknown names untouched", () => {
    const text = "see /usr/bin, https://host/rewrite and /unknown";
    expect(expandSkillReferences(text, skills)).toBe(text);
  });

  it("expands nested references", () => {
    expect(expandSkillReferences("/polite", skills)).toBe(
      "Be polite. Rewrite the text clearly.",
    );
  });

  it("does not recurse into a cycle", () => {
    expect(expandSkillReferences("/loop", skills)).toBe("Again /loop");
  });

  it("returns the text unchanged when there are no skills", () => {
    expect(expandSkillReferences("/rewrite", new Map())).toBe("/rewrite");
  });
});
