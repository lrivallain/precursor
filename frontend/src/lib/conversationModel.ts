import type { ConversationLLMSelection, Settings } from "./types";

/** Which model a conversation runs on, and where to look it up. */
export interface ResolvedModel {
  /** Model id, or null when nothing is configured yet. */
  id: string | null;
  /** Provider whose catalog lists `id`. */
  provider: string | null;
  /** Context budget set by a category preset; null means "use the catalog's". */
  contextTokens: number | null;
}

type ModelSettings = Pick<
  Settings,
  "llm_provider" | "llm_model" | "llm_model_category" | "llm_category_preset" | "model_fallbacks"
>;

/**
 * The app-wide default model. A default category overrides `llm_model`, and its
 * preset may live on another provider than `llm_provider`, so the catalog to
 * search comes from the preset. Mirrors `preview_llm_category_preset` and
 * `resolve_llm_preparation_budget` on the backend.
 */
export function resolveDefaultModel(settings: ModelSettings | null | undefined): ResolvedModel {
  if (!settings) return { id: null, provider: null, contextTokens: null };
  const preset = settings.llm_model_category ? settings.llm_category_preset : null;
  if (preset) {
    // "agents" is the Copilot SDK runtime, which has no model catalog.
    const provider = preset.provider === "agents" ? settings.llm_provider : preset.provider;
    return { id: preset.model, provider, contextTokens: preset.context_tokens };
  }
  return { id: settings.llm_model || null, provider: settings.llm_provider, contextTokens: null };
}

/** The model a conversation runs on: its own selection, else the default. */
export function resolveConversationModel(
  settings: ModelSettings | null | undefined,
  selection: ConversationLLMSelection | null | undefined,
): ResolvedModel {
  if (!selection) return resolveDefaultModel(settings);
  const presets = selection.category
    ? (settings?.model_fallbacks?.categories.find((entry) => entry.id === selection.category)?.presets ?? [])
        .filter((preset) => preset.provider !== "agents")
    : [];
  return {
    id: selection.model || presets[0]?.model || null,
    provider: presets[0]?.provider || selection.provider,
    contextTokens: presets.length
      ? Math.max(...presets.map((preset) => preset.context_tokens))
      : selection.context_tokens,
  };
}
