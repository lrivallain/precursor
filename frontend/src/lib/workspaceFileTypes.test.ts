import { describe, expect, it } from "vitest";
import { hasPreview, isEditable, isImage, isMermaid, isSvg } from "./workspaceFileTypes";

describe("workspace file types", () => {
  it("previews and edits canvas sources", () => {
    for (const name of ["a.mmd", "a.svg", "a.canvas.html", "a.drawio", "a.md"]) {
      expect(hasPreview(name)).toBe(true);
      expect(isEditable(name)).toBe(true);
    }
    expect(isMermaid("FLOW.MMD")).toBe(true);
    expect(isSvg("logo.svg")).toBe(true);
    expect(isImage("logo.svg")).toBe(false);
    expect(hasPreview("a.py")).toBe(false);
  });
});
