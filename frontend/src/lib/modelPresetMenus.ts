import type { ModelCategories, ModelCategory, ModelPreset } from "./types";

export const AGENTS_PROVIDER = "agents";

// Offered once, when no category exists yet.
export const QUICK_START_CATEGORIES: { id: ModelCategory; label: string; hint: string }[] = [
  { id: "efficiency", label: "Efficiency", hint: "Fast, economical tasks" },
  { id: "balanced", label: "Balanced", hint: "Everyday work" },
  { id: "intelligence", label: "Intelligence", hint: "Complex reasoning" },
];

export interface ModelPresetMenuEntry {
  value: string;
  category: ModelCategory;
  label: string;
  presets: ModelPreset[];
}

export function categoryLabel(categories: ModelCategories | undefined, id: ModelCategory): string {
  return categories?.categories.find((entry) => entry.id === id)?.label ?? id;
}

export function slugifyCategory(label: string, taken: Set<string>): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 36) || "category";
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}-${n}`;
  return id;
}

/** Menu entries for the categories that hold at least one preset in this scope. */
export function modelPresetMenuEntries(categories: ModelCategories | undefined, agents: boolean, modelIds: string[] = []): ModelPresetMenuEntry[] {
  const taken = new Set(modelIds);
  return (categories?.categories ?? []).flatMap((category) => {
    const presets = category.presets.filter((preset) => (preset.provider === AGENTS_PROVIDER) === agents);
    if (!presets.length) return [];
    let value = `preset:${agents ? "agents" : "llm"}:${category.id}`;
    while (taken.has(value)) value = `:${value}`;
    taken.add(value);
    return [{ value, category: category.id, label: category.label, presets }];
  });
}

export function selectedModelCategoryEntry(entries: ModelPresetMenuEntry[], category: ModelCategory | null | undefined): ModelPresetMenuEntry | undefined {
  return entries.find((entry) => entry.category === category);
}

export function providerModelMenuValue(provider: string, kind: "model" | "preset", id: string): string {
  return JSON.stringify([provider, kind, id]);
}
