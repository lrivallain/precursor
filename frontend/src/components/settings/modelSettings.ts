import type { LLMProviderSpec, Settings, SettingsUpdate } from "../../lib/types";

export type ProviderConfig = Record<string, Record<string, string>>;

/** Marker for a stored provider secret queued for removal: "provider.field". */
export function secretMarker(provider: string, field: string): string {
  return `${provider}.${field}`;
}

/** Add the marker when absent, remove it when present. */
export function toggleMarker(markers: readonly string[], marker: string): string[] {
  return markers.includes(marker) ? markers.filter((m) => m !== marker) : [...markers, marker];
}

/**
 * The `llm_providers` payload from the edited provider config. Empty secret
 * fields are left out so a blank password input never clears a saved secret;
 * a secret queued for removal is sent as "" unless a new value was typed.
 */
export function buildLlmProvidersPayload(
  providers: readonly LLMProviderSpec[],
  config: ProviderConfig,
  clearedSecrets: readonly string[],
): ProviderConfig {
  const secretFields = new Map(
    providers.map((spec) => [spec.id, new Set(spec.fields.filter((f) => f.secret).map((f) => f.name))]),
  );
  const out: ProviderConfig = {};
  for (const [pid, cfg] of Object.entries(config)) {
    const entry: Record<string, string> = {};
    for (const [k, v] of Object.entries(cfg)) {
      if (secretFields.get(pid)?.has(k) && v === "") continue;
      entry[k] = v;
    }
    for (const marker of clearedSecrets) {
      const [cleared, field] = marker.split(".");
      if (cleared === pid && !entry[field]) entry[field] = "";
    }
    if (Object.keys(entry).length > 0) out[pid] = entry;
  }
  return out;
}

/**
 * The default-model fields to send. Changing the default provider resets the
 * model (the old one belongs to another catalog); picking a new model pins it
 * and drops any preset category. Otherwise nothing is sent, so the server
 * keeps a categorised pin or resolves a retired one.
 */
export function defaultModelPayload(
  saved: Pick<Settings, "llm_provider" | "llm_model"> | null,
  defaultProvider: string,
  defaultModel: string,
): SettingsUpdate {
  if (defaultProvider !== saved?.llm_provider) {
    return { llm_model: "", llm_model_category: null, llm_reasoning_effort: "" };
  }
  if (defaultModel && defaultModel !== saved?.llm_model) {
    return { llm_model: defaultModel, llm_model_category: null, llm_reasoning_effort: "" };
  }
  return {};
}

/** The model the "Global default model" picker shows: "" while a category is pinned. */
export function pinnedDefaultModel(s: Pick<Settings, "llm_model" | "llm_model_category">): string {
  return s.llm_model_category ? "" : s.llm_model;
}
