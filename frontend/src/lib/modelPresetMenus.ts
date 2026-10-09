import type { ModelCategories, ModelCategory, ModelPreset } from "./types";

export const MODEL_PRESET_CATEGORIES: { id: ModelCategory; label: string; hint: string }[] = [
  { id: "efficiency", label: "Efficiency", hint: "Fast, economical tasks" },
  { id: "balanced", label: "Balanced", hint: "Everyday work" },
  { id: "intelligence", label: "Intelligence", hint: "Complex reasoning" },
];

export interface ModelPresetMenuEntry {
  value: string;
  category: ModelCategory;
  preset: ModelPreset;
}

export function modelPresetMenuEntries(categories: ModelCategories | undefined, agents: boolean, modelIds: string[] = []): ModelPresetMenuEntry[] {
  const configuredModels = MODEL_PRESET_CATEGORIES.flatMap((category) => categories?.[category.id] ?? []).map((preset) => preset.model);
  const taken = new Set([...modelIds, ...configuredModels]);
  return MODEL_PRESET_CATEGORIES.flatMap((category) => (categories?.[category.id] ?? []).map((preset, index) => {
    let value = `preset:${agents ? "agents" : "llm"}:${category.id}:${index}`;
    while (taken.has(value)) value = `:${value}`;
    taken.add(value);
    return { value, category: category.id, preset };
  }));
}

export function matchingModelPreset(entries: ModelPresetMenuEntry[], selection: {
  model: string;
  reasoning_effort: string;
  context_tokens?: number;
  context_tier?: string;
}, agents: boolean): ModelPresetMenuEntry | undefined {
  return entries.find(({ preset }) =>
    preset.model === selection.model
    && preset.reasoning_effort === selection.reasoning_effort
    && (agents ? preset.context_tier === selection.context_tier : preset.context_tokens === selection.context_tokens),
  );
}
