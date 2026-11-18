import { describe, expect, it } from "vitest";
import { accountOptions, showAccountPicker } from "./githubAccounts";

const two = {
  accounts: [
    { login: "personal", active: true },
    { login: "work", active: false },
  ],
  selected: "",
  env_pinned: "",
};

describe("showAccountPicker", () => {
  it("shows only when there is a choice or a pick to clear", () => {
    expect(showAccountPicker(null)).toBe(false);
    expect(showAccountPicker({ ...two, accounts: [two.accounts[0]] })).toBe(false);
    expect(showAccountPicker(two)).toBe(true);
    expect(showAccountPicker({ ...two, accounts: [two.accounts[0]], selected: "work" })).toBe(true);
  });
});

describe("accountOptions", () => {
  it("names the account the default resolves to", () => {
    expect(accountOptions(two).map((o) => o.label)).toEqual([
      "Default (@personal, active in gh)",
      "@personal",
      "@work",
    ]);
  });

  it("prefers the env pin for the default", () => {
    expect(accountOptions({ ...two, env_pinned: "work" })[0].label).toBe(
      "Default (@work, from PRECURSOR_GITHUB_CLI_USER)",
    );
  });

  it("keeps a signed-out pick so it can be cleared", () => {
    const options = accountOptions({ ...two, selected: "gone" });
    expect(options.at(-1)).toEqual({ value: "gone", label: "@gone (not signed in to gh)" });
  });
});
