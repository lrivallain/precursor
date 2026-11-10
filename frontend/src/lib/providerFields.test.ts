import { describe, expect, it } from "vitest";
import { providerFieldValue, shownProviderFields } from "./providerFields";
import type { LLMProviderField } from "./types";

const field = (name: string, extra: Partial<LLMProviderField> = {}): LLMProviderField => ({
  name,
  label: name,
  secret: false,
  required: false,
  placeholder: "",
  help: "",
  choices: [],
  shown_when: null,
  ...extra,
});

const fields = [
  field("endpoint"),
  field("auth", {
    choices: [
      { value: "azure_cli", label: "Azure CLI" },
      { value: "key", label: "API key" },
    ],
  }),
  field("key", { secret: true, shown_when: { field: "auth", value: "key" } }),
  field("tenant", { shown_when: { field: "auth", value: "azure_cli" } }),
];

const names = (values?: Record<string, string>) => shownProviderFields(fields, values).map((f) => f.name);

describe("providerFieldValue", () => {
  it("shows a dropdown's first option until one is picked", () => {
    expect(providerFieldValue(fields[1], undefined)).toBe("azure_cli");
    expect(providerFieldValue(fields[1], { auth: "key" })).toBe("key");
  });

  it("leaves a plain field empty", () => {
    expect(providerFieldValue(fields[0], {})).toBe("");
  });
});

describe("shownProviderFields", () => {
  it("shows only the fields of the picked sign-in", () => {
    expect(names()).toEqual(["endpoint", "auth", "tenant"]);
    expect(names({ auth: "azure_cli" })).toEqual(["endpoint", "auth", "tenant"]);
    expect(names({ auth: "key" })).toEqual(["endpoint", "auth", "key"]);
  });
});
