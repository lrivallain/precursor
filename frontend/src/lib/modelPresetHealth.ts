import type { LLMProviderSpec, ModelCategoryDef, ModelPreset, Settings } from "./types";

export interface CatalogModel {
  id: string;
  name?: string;
  context_window?: number | null;
  supported_reasoning_efforts?: string[];
}

export interface CatalogCheck {
  state: "ready" | "checking" | "setup" | "unavailable" | "unsupported" | "unchecked";
  detail: string;
  models: CatalogModel[];
}

export interface PresetHealth {
  listed: boolean;
  issues: string[];
  catalog: CatalogCheck["state"];
}

export interface PresetSummary {
  state: "valid" | "review" | "checking" | "setup" | "unverified" | "empty";
  label: string;
  detail: string;
  listed: number;
  total: number;
}

export function providerSetupIssue(settings: Settings | null, provider: LLMProviderSpec | undefined): string | null {
  if (!settings || !provider) return null;
  if (provider.id === "mock" && settings.mock_provider_enabled === false) {
    return "Enable Mock in Settings > Model, then save.";
  }
  if (provider.uses_github_token && settings.github_token_source === "none") {
    return "Connect GitHub in Settings > GitHub, then apply and refresh the provider.";
  }
  const missing = provider.fields.filter((field) => field.required && (
    field.secret
      ? !settings.llm_providers_present?.[provider.id]?.[field.name]
      : !settings.llm_providers?.[provider.id]?.[field.name]?.trim()
  ));
  return missing.length
    ? `Configure ${missing.map((field) => field.label).join(", ")} for ${provider.label}, then Apply & refresh models.`
    : null;
}

export function providerConfigurationLabel(settings: Settings | null, provider: LLMProviderSpec): string {
  const status = !settings ? "Checking configuration"
    : providerSetupIssue(settings, provider) ? "\u25cb Not configured" : "\u2713 Configured";
  return `${provider.label} \u00b7 ${status}`;
}

const AGENTS = "agents";

function signature(preset: ModelPreset): string {
  return JSON.stringify([
    preset.provider, preset.model.trim(), preset.reasoning_effort,
    preset.provider === AGENTS ? preset.context_tier : preset.context_tokens,
  ]);
}

/** Checks every preset against the catalogue of its own provider. */
export function checkCategories(categories: ModelCategoryDef[], catalogs: Record<string, CatalogCheck | undefined>): Record<string, PresetHealth[]> {
  const signatures = new Map<string, number>();
  for (const preset of categories.flatMap((category) => category.presets)) {
    const key = signature(preset);
    signatures.set(key, (signatures.get(key) ?? 0) + 1);
  }
  const check = (preset: ModelPreset): PresetHealth => {
    const catalog = catalogs[preset.provider] ?? { state: "unchecked", detail: "Catalogue not read yet.", models: [] };
    const agents = preset.provider === AGENTS;
    const id = preset.model.trim();
    const issues: string[] = [];
    if (!id) issues.push("Select a model for this preset.");
    else if (id === "auto") issues.push("Choose an explicit model; Auto cannot be categorised.");
    if (!agents && (!Number.isInteger(preset.context_tokens) || preset.context_tokens < 1000 || preset.context_tokens > 5_000_000)) {
      issues.push("Set a context budget between 1,000 and 5,000,000 tokens.");
    }
    if (id && (signatures.get(signature(preset)) ?? 0) > 1) {
      issues.push("This provider, model, effort and context preset is duplicated in another slot.");
    }
    if (catalog.state === "setup") issues.push(catalog.detail);
    const model = catalog.state === "ready" && id !== "auto"
      ? catalog.models.find((entry) => entry.id === id)
      : undefined;
    if (catalog.state === "ready" && id && id !== "auto" && !model) {
      issues.push("Model not listed in the current catalogue. Keep a retired id for category matching, but add a listed alternative.");
    }
    if (model && preset.reasoning_effort) {
      const efforts = model.supported_reasoning_efforts ?? [];
      if (!efforts.length) {
        issues.push("Effort support is not advertised. Use Auto or review this preset.");
      } else if (!efforts.includes(preset.reasoning_effort)) {
        issues.push(`Effort "${preset.reasoning_effort}" is not advertised for this model. Choose ${efforts.join(", ")} or Auto.`);
      }
    }
    if (!agents && model?.context_window && preset.context_tokens > model.context_window) {
      issues.push(`Context budget exceeds the advertised ${model.context_window.toLocaleString()}-token window. Fallback requests are capped to that window.`);
    }
    return { listed: Boolean(model), issues, catalog: catalog.state };
  };
  return Object.fromEntries(categories.map((category) => [category.id, category.presets.map(check)]));
}

export function summarizePresets(checks: PresetHealth[]): PresetSummary {
  const total = checks.length;
  const listed = checks.filter((check) => check.listed).length;
  const review = checks.filter((check) => check.issues.length).length;
  const base = { total, listed };
  if (!total) return { ...base, state: "empty", label: "Empty", detail: "Add at least one model so the engine has something to choose from." };
  if (review) return { ...base, state: "review", label: `${review} ${review === 1 ? "needs" : "need"} review`, detail: `${review} of ${total} models need review.` };
  if (checks.some((check) => check.catalog === "checking")) return { ...base, state: "checking", label: "Checking...", detail: "Reading model catalogues; no inference request is made." };
  if (checks.some((check) => check.catalog !== "ready")) return { ...base, state: "unverified", label: "Not checked", detail: "Some catalogues could not be read, so those models are unverified." };
  return { ...base, state: "valid", label: `${listed}/${total} listed`, detail: "Every model is listed. Catalogue membership does not guarantee a successful inference request." };
}
