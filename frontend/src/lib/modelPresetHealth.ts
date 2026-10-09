import type { LLMProviderSpec, ModelCategories, ModelPreset, Settings } from "./types";

export interface CatalogModel {
  id: string;
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

export function checkPresets(categories: ModelCategories, catalog: CatalogCheck, agents: boolean): Record<keyof ModelCategories, PresetHealth[]> {
  const signature = (preset: ModelPreset): string => JSON.stringify([
    preset.model.trim(), preset.reasoning_effort, agents ? preset.context_tier : preset.context_tokens,
  ]);
  const signatures = new Map<string, number>();
  for (const preset of Object.values(categories).flat()) {
    const key = signature(preset);
    signatures.set(key, (signatures.get(key) ?? 0) + 1);
  }
  const check = (preset: ModelPreset): PresetHealth => {
    const id = preset.model.trim();
    const issues: string[] = [];
    if (!id) issues.push("Select a model for this preset.");
    else if (id === "auto") issues.push("Choose an explicit model; Auto cannot be categorised.");
    if (!Number.isInteger(preset.context_tokens) || preset.context_tokens < 1000 || preset.context_tokens > 5_000_000) {
      issues.push("Set a context budget between 1,000 and 5,000,000 tokens.");
    }
    if (id && (signatures.get(signature(preset)) ?? 0) > 1) {
      issues.push("This model, effort and context preset is duplicated in this configuration.");
    }
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
    return { listed: Boolean(model), issues };
  };
  return {
    efficiency: categories.efficiency.map(check),
    balanced: categories.balanced.map(check),
    intelligence: categories.intelligence.map(check),
  };
}

export function summarizePresets(checks: PresetHealth[], catalog: CatalogCheck): PresetSummary {
  const total = checks.length;
  const listed = checks.filter((check) => check.listed).length;
  const review = checks.filter((check) => check.issues.length).length;
  const base = { total, listed };
  if (!total) return { ...base, state: "empty", label: "Not configured", detail: "Optional: add presets to enable alternatives for this category." };
  if (review) return {
    ...base, state: "review", label: `${review} ${review === 1 ? "needs" : "need"} review`,
    detail: `${review} of ${total} presets need review. ${catalog.state === "ready" ? `${listed} of ${total} model ids are listed.` : catalog.detail}`,
  };
  if (catalog.state === "checking") return { ...base, state: "checking", label: "Checking...", detail: "Reading the model catalogue; no inference request is made." };
  if (catalog.state === "setup") return { ...base, state: "setup", label: "Setup required", detail: catalog.detail };
  if (catalog.state !== "ready") return { ...base, state: "unverified", label: "Not checked", detail: catalog.detail };
  return { ...base, state: "valid", label: `${listed}/${total} listed`, detail: "All preset model ids are listed. Catalogue membership does not guarantee a successful inference request." };
}

export function summarizePresetConfigurations(configurations: { label: string; checks: PresetHealth[]; catalog: CatalogCheck }[]): PresetSummary {
  const active = configurations.filter((configuration) => configuration.checks.length > 0);
  const total = active.reduce((sum, configuration) => sum + configuration.checks.length, 0);
  const listed = active.reduce((sum, configuration) => sum + configuration.checks.filter((check) => check.listed).length, 0);
  const review = active.reduce((sum, configuration) => sum + configuration.checks.filter((check) => check.issues.length).length, 0);
  const summaries = active.map((configuration) => summarizePresets(configuration.checks, configuration.catalog));
  const detail = active.map((configuration, index) => `${configuration.label}: ${summaries[index].detail}`).join("\n");
  const base = { total, listed, detail };
  if (!total) return { ...base, state: "empty", label: "Not configured", detail: "No model alternatives are configured. Both runtimes are optional." };
  if (review) return { ...base, state: "review", label: `${review} ${review === 1 ? "preset needs" : "presets need"} review` };
  if (summaries.some((summary) => summary.state === "checking")) return { ...base, state: "checking", label: "Checking..." };
  if (summaries.some((summary) => summary.state === "setup")) return { ...base, state: "setup", label: "Setup required" };
  if (summaries.some((summary) => summary.state === "unverified")) return { ...base, state: "unverified", label: "Checks incomplete" };
  return { ...base, state: "valid", label: `${listed}/${total} model ids listed` };
}
