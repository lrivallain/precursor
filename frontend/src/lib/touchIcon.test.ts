import { describe, expect, it } from "vitest";
import { needsInlineIcon } from "./touchIcon";

describe("needsInlineIcon", () => {
  it("leaves local pages alone", () => {
    expect(needsInlineIcon("localhost")).toBe(false);
    expect(needsInlineIcon("127.0.0.1")).toBe(false);
  });
  it("inlines behind a tunnel or proxy", () => {
    expect(needsInlineIcon("abc-8000.euw.devtunnels.ms")).toBe(true);
  });
});
