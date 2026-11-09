import { describe, expect, it } from "vitest";
import { resolveConversationModel, resolveDefaultModel } from "./conversationModel";
import type { ConversationLLMSelection, ModelPreset, Settings } from "./types";

function preset(provider: string, model: string, context_tokens: number): ModelPreset {
  return { provider, model, context_tokens, reasoning_effort: "", context_tier: "default" };
}

const opus = preset("azure_foundry", "claude-opus-5-5", 512_000);

function settings(overrides: Partial<Settings> = {}): Settings {
  return {
    llm_provider: "github_copilot",
    llm_model: "claude-sonnet-5.5",
    llm_model_category: null,
    llm_category_preset: null,
    model_fallbacks: {
      categories: [
        { id: "new-category", label: "New", presets: [opus] },
        {
          id: "mixed",
          label: "Mixed",
          presets: [preset("agents", "auto", 999_000), preset("openai", "gpt", 128_000), preset("openai", "gpt-mini", 64_000)],
        },
      ],
    },
    ...overrides,
  } as Settings;
}

function selection(overrides: Partial<ConversationLLMSelection> = {}): ConversationLLMSelection {
  return {
    provider: "github_copilot",
    model: "",
    category: null,
    reasoning_effort: "",
    context_tokens: 100_000,
    ...overrides,
  };
}

describe("resolveDefaultModel", () => {
  it("has nothing before settings load", () => {
    expect(resolveDefaultModel(null)).toEqual({ id: null, provider: null, contextTokens: null });
  });

  it("uses llm_model on llm_provider without a default category", () => {
    expect(resolveDefaultModel(settings())).toEqual({
      id: "claude-sonnet-5.5",
      provider: "github_copilot",
      contextTokens: null,
    });
  });

  it("looks a default category's preset up on the preset's own provider", () => {
    const resolved = resolveDefaultModel(settings({ llm_model_category: "new-category", llm_category_preset: opus }));
    expect(resolved).toEqual({ id: "claude-opus-5-5", provider: "azure_foundry", contextTokens: 512_000 });
  });

  it("keeps llm_provider's catalog for an agents preset", () => {
    const resolved = resolveDefaultModel(
      settings({ llm_model_category: "mixed", llm_category_preset: preset("agents", "auto", 999_000) }),
    );
    expect(resolved).toEqual({ id: "auto", provider: "github_copilot", contextTokens: 999_000 });
  });

  it("ignores a stale preset once the category is cleared", () => {
    expect(resolveDefaultModel(settings({ llm_category_preset: opus })).id).toBe("claude-sonnet-5.5");
  });
});

describe("resolveConversationModel", () => {
  it("falls back to the default model without a selection", () => {
    const s = settings({ llm_model_category: "new-category", llm_category_preset: opus });
    expect(resolveConversationModel(s, null)).toEqual(resolveDefaultModel(s));
  });

  it("uses a direct selection's model and budget", () => {
    expect(resolveConversationModel(settings(), selection({ provider: "openai", model: "gpt" }))).toEqual({
      id: "gpt",
      provider: "openai",
      contextTokens: 100_000,
    });
  });

  it("takes a category selection's largest non-agents budget", () => {
    expect(resolveConversationModel(settings(), selection({ category: "mixed" }))).toEqual({
      id: "gpt",
      provider: "openai",
      contextTokens: 128_000,
    });
  });
});
