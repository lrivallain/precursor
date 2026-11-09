import { describe, expect, it } from "vitest";
import { clamp, parseClampedInt } from "./resizeCore";

describe("clamp", () => {
  it("keeps values inside the range", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-3, 0, 10)).toBe(0);
    expect(clamp(42, 0, 10)).toBe(10);
  });

  it("returns min when the range is inverted (e.g. a viewport smaller than min)", () => {
    expect(clamp(50, 100, 80)).toBe(100);
  });
});

describe("parseClampedInt", () => {
  const parse = parseClampedInt(100, 400);

  it("parses and clamps a stored integer", () => {
    expect(parse("250")).toBe(250);
    expect(parse("9999")).toBe(400);
    expect(parse("12")).toBe(100);
  });

  it("returns null for a value that isn't a number", () => {
    expect(parse("wide")).toBeNull();
  });
});
