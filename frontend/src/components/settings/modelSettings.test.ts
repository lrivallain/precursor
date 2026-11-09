import { describe, expect, it } from "vitest";
import type { LLMProviderSpec } from "../../lib/types";
import {
  buildLlmProvidersPayload,
  defaultModelPayload,
  pinnedDefaultModel,
  secretMarker,
  toggleMarker,
} from "./modelSettings";

const spec = (id: string, fields: Array<{ name: string; secret?: boolean }>) =>
  ({ id, fields: fields.map((f) => ({ secret: false, ...f })) }) as unknown as LLMProviderSpec;

const providers = [spec("azure", [{ name: "endpoint" }, { name: "api_key", secret: true }])];

describe("buildLlmProvidersPayload", () => {
  it("leaves out blank secrets so a saved key is kept", () => {
    expect(buildLlmProvidersPayload(providers, { azure: { endpoint: "https://x", api_key: "" } }, []))
      .toEqual({ azure: { endpoint: "https://x" } });
  });

  it("keeps blank non-secret fields, which clear them", () => {
    expect(buildLlmProvidersPayload(providers, { azure: { endpoint: "" } }, [])).toEqual({ azure: { endpoint: "" } });
  });

  it("sends a secret queued for removal as an empty string", () => {
    expect(buildLlmProvidersPayload(providers, { azure: { api_key: "" } }, ["azure.api_key"]))
      .toEqual({ azure: { api_key: "" } });
  });

  it("prefers a newly typed secret over a queued removal", () => {
    expect(buildLlmProvidersPayload(providers, { azure: { api_key: "new" } }, ["azure.api_key"]))
      .toEqual({ azure: { api_key: "new" } });
  });

  it("drops providers with nothing to send", () => {
    expect(buildLlmProvidersPayload(providers, { azure: { api_key: "" } }, [])).toEqual({});
  });
});

describe("defaultModelPayload", () => {
  const saved = { llm_provider: "github", llm_model: "gpt-4o" };

  it("resets the model when the default provider changes", () => {
    expect(defaultModelPayload(saved, "azure", "gpt-4o")).toEqual({
      llm_model: "",
      llm_model_category: null,
      llm_reasoning_effort: "",
    });
  });

  it("pins a newly picked model and drops the category", () => {
    expect(defaultModelPayload(saved, "github", "o3")).toEqual({
      llm_model: "o3",
      llm_model_category: null,
      llm_reasoning_effort: "",
    });
  });

  it("sends nothing when the model is unchanged or unset", () => {
    expect(defaultModelPayload(saved, "github", "gpt-4o")).toEqual({});
    expect(defaultModelPayload(saved, "github", "")).toEqual({});
  });
});

describe("pinnedDefaultModel", () => {
  it("is empty while a preset category is pinned", () => {
    expect(pinnedDefaultModel({ llm_model: "gpt-4o", llm_model_category: "fast" as never })).toBe("");
    expect(pinnedDefaultModel({ llm_model: "gpt-4o", llm_model_category: null })).toBe("gpt-4o");
  });
});

describe("toggleMarker", () => {
  it("adds then removes a marker", () => {
    const marker = secretMarker("azure", "api_key");
    expect(marker).toBe("azure.api_key");
    expect(toggleMarker([], marker)).toEqual([marker]);
    expect(toggleMarker([marker, "x.y"], marker)).toEqual(["x.y"]);
  });
});
