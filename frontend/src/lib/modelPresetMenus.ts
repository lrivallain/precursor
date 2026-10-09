import type { ModelCategories, ModelCategory, ModelPreset } from "./types";

export const MODEL_PRESET_CATEGORIES: { id: ModelCategory; label: string; hint: string }[] = [
  { id: "efficiency", label: "Efficiency", hint: "Fast, economical tasks" },
  { id: "balanced", label: "Balanced", hint: "Everyday work" },
  { id: "intelligence", label: "Intelligence", hint: "Complex reasoning" },
];

export interface ModelPresetMenuEntry {
  value: string;
  category: ModelCategory;
  presets: ModelPreset[];
}

export function modelPresetMenuEntries(categories: ModelCategories | undefined, agents: boolean, modelIds: string[] = []): ModelPresetMenuEntry[] {
  const configuredModels = MODEL_PRESET_CATEGORIES.flatMap((category) => categories?.[category.id] ?? []).map((preset) => preset.model);
  const taken = new Set([...modelIds, ...configuredModels]);
  return MODEL_PRESET_CATEGORIES.flatMap((category) => {
    const presets = categories?.[category.id] ?? [];
    if (!presets.length) return [];
    let value = `preset:${agents ? "agents" : "llm"}:${category.id}`;
    while (taken.has(value)) value = `:${value}`;
    taken.add(value);
    return [{ value, category: category.id, presets }];
  });
}

export function selectedModelCategoryEntry(entries: ModelPresetMenuEntry[], category: ModelCategory | null | undefined): ModelPresetMenuEntry | undefined {
  return entries.find((entry) => entry.category === category);
}
