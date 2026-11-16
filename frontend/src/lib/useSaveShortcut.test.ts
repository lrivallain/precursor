import { describe, expect, it } from "vitest";
import { isSaveShortcut } from "./useSaveShortcut";

const key = (k: string, mods: Partial<KeyboardEvent> = {}) => ({
  key: k,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  ...mods,
});

describe("isSaveShortcut", () => {
  it("accepts Cmd+S and Ctrl+S", () => {
    expect(isSaveShortcut(key("s", { metaKey: true }))).toBe(true);
    expect(isSaveShortcut(key("s", { ctrlKey: true }))).toBe(true);
    expect(isSaveShortcut(key("S", { ctrlKey: true }))).toBe(true);
  });

  it("rejects a bare S and other combinations", () => {
    expect(isSaveShortcut(key("s"))).toBe(false);
    expect(isSaveShortcut(key("s", { metaKey: true, shiftKey: true }))).toBe(false);
    expect(isSaveShortcut(key("s", { ctrlKey: true, altKey: true }))).toBe(false);
    expect(isSaveShortcut(key("d", { metaKey: true }))).toBe(false);
  });
});
