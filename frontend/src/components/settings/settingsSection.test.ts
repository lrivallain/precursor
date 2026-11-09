import { describe, expect, it } from "vitest";
import type { Settings } from "../../lib/types";
import { formSnapshot, mergeSectionPayloads, savedSnapshots, type SettingsSection } from "./settingsSection";

describe("mergeSectionPayloads", () => {
  it("combines every tab's fields", () => {
    const sections: SettingsSection[] = [
      { payload: () => ({ theme: "dark" }) },
      {},
      { payload: () => ({ github_repo: "o/r", max_tool_rounds: 3 }) },
    ];
    expect(mergeSectionPayloads(sections)).toEqual({ theme: "dark", github_repo: "o/r", max_tool_rounds: 3 });
  });

  it("merges api_keys from several tabs instead of letting the last win", () => {
    const sections: SettingsSection[] = [
      { payload: () => ({ api_keys: { github_token: "ghp" } }) },
      { payload: () => ({ api_keys: { azure_speech_key: "az" } }) },
    ];
    expect(mergeSectionPayloads(sections).api_keys).toEqual({ github_token: "ghp", azure_speech_key: "az" });
  });

  it("omits api_keys when no tab sends a secret", () => {
    expect(mergeSectionPayloads([{ payload: () => ({ theme: "light" }) }])).not.toHaveProperty("api_keys");
  });
});

describe("savedSnapshots", () => {
  const updated = {} as Settings;

  it("keeps a tab's snapshot unless onSaved returns a new one", () => {
    const sections: SettingsSection[] = [
      { snapshot: ["a"] },
      { snapshot: ["b"], onSaved: () => undefined },
      { snapshot: ["token"], onSaved: () => [""] },
    ];
    expect(savedSnapshots(sections, updated, {})).toEqual([["a"], ["b"], [""]]);
  });

  it("hands each tab the saved settings and the payload that was sent", () => {
    const seen: unknown[] = [];
    savedSnapshots([{ onSaved: (u, sent) => { seen.push(u, sent); } }], updated, { theme: "dark" });
    expect(seen).toEqual([updated, { theme: "dark" }]);
  });
});

describe("formSnapshot", () => {
  it("changes when any tab's value changes", () => {
    expect(formSnapshot([[1, "a"], { x: true }])).not.toBe(formSnapshot([[1, "a"], { x: false }]));
    expect(formSnapshot([[1, "a"]])).toBe(formSnapshot([[1, "a"]]));
  });
});
