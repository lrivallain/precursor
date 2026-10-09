import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { modelsStore, useCurrentModel, useModelsVersion } from "../lib/modelsStore";
import { settingsStore, useSettings } from "../lib/settingsStore";
import type { AgentModelInfo, LLMModel } from "../lib/types";
import { matchingModelPreset, modelPresetMenuEntries, MODEL_PRESET_CATEGORIES, type ModelPresetMenuEntry } from "../lib/modelPresetMenus";
import { ComposerSelectMenu, type MenuGroup, type MenuOption } from "./ComposerSelectMenu";

const EFFORT_LABELS: Record<string, string> = {
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
};

// Context-size presets. For a given model we keep only the three largest that
// fit its window (plus the window itself as "Max") — tiny budgets make no sense
// on a large-context model.
const CONTEXT_TIERS = [16_000, 32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000];
const CONTEXT_OPTION_COUNT = 3;

function effortLabel(value: string): string {
  return EFFORT_LABELS[value] ?? value.charAt(0).toUpperCase() + value.slice(1);
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

export function groupModelsByPublisher(models: LLMModel[]): MenuGroup[] {
  const grouped = models.reduce<Record<string, LLMModel[]>>((acc, model) => {
    (acc[model.publisher || "Other"] ||= []).push(model);
    return acc;
  }, {});
  return Object.entries(grouped).map(([label, list]) => ({
    label,
    options: list.map((model) => ({ value: model.id, label: model.name })),
  }));
}

function presetModelGroups(entries: ModelPresetMenuEntry[], models: { id: string; name: string }[], agents: boolean, catalogKnown: boolean): MenuGroup[] {
  return MODEL_PRESET_CATEGORIES.map((category) => ({
    parentLabel: "Presets",
    label: category.label,
    options: entries.filter((entry) => entry.category === category.id).map(({ value, preset }) => {
      const model = models.find((model) => model.id === preset.model);
      const context = agents
        ? (preset.context_tier === "long_context" ? "Long context" : "Default context")
        : `${formatTokens(preset.context_tokens)} context`;
      return {
        value,
        label: model?.name ?? preset.model,
        description: `${preset.reasoning_effort ? effortLabel(preset.reasoning_effort) : "Auto"} effort · ${context}${!model && catalogKnown ? " · Not listed" : ""}`,
        searchText: preset.model,
      };
    }),
  })).filter((group) => group.options.length > 0);
}

// Ascending list of the (up to three) context-budget values offered for a model
// with the given window. The window itself is always the top value.
function contextValuesForModel(maxCtx: number | null | undefined): number[] {
  const cap = maxCtx && maxCtx > 0 ? maxCtx : undefined;
  const candidates = new Set<number>();
  for (const t of CONTEXT_TIERS) {
    if (!cap || t < cap) candidates.add(t);
  }
  if (cap) candidates.add(cap);
  return [...candidates]
    .filter((v) => v > 0)
    .sort((a, b) => b - a)
    .slice(0, CONTEXT_OPTION_COUNT)
    .sort((a, b) => a - b);
}

function contextOptions(maxCtx: number | null | undefined): MenuOption[] {
  const values = contextValuesForModel(maxCtx);
  const top = values[values.length - 1];
  return values.map((v) => ({
    value: String(v),
    label: v === top && maxCtx && maxCtx > 0 ? `${formatTokens(v)} · Max` : formatTokens(v),
  }));
}

// Snap a stored budget onto the values valid for a model: the largest option
// that doesn't exceed the current budget, or the smallest option when the
// current budget is below them all. Keeps the selection valid after a switch.
function snapContextForModel(maxCtx: number | null | undefined, current: number): number {
  const values = contextValuesForModel(maxCtx);
  if (values.length === 0) return current;
  const fitting = values.filter((v) => v <= current);
  return fitting.length ? Math.max(...fitting) : Math.min(...values);
}

/**
 * Compact model + reasoning-effort + context-size pickers rendered inside the
 * composer toolbar. The `llm` variant writes the *global* LLM defaults to app
 * settings (mirroring Settings → Model); the `agents` variant writes the SDK
 * agents' default model (Settings → Agents). Kept self-contained — it
 * reads/writes the shared stores directly so the dumb `Composer` doesn't need
 * new props.
 */
export function ComposerModelControls({
  variant = "llm",
}: {
  variant?: "llm" | "agents";
}) {
  if (variant === "agents") return <AgentModelControls />;
  return <LlmModelControls />;
}

function LlmModelControls() {
  useModelsVersion();
  const currentModel = useCurrentModel();
  const settings = useSettings();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void modelsStore.ensureLoaded();
  }, []);

  const models = modelsStore.all();
  const effort = settings?.llm_reasoning_effort ?? "";
  const modelId = currentModel?.id ?? settings?.llm_model ?? "";
  const selectedModel = currentModel ?? models.find((m) => m.id === modelId) ?? null;

  async function save(patch: {
    llm_model?: string;
    llm_reasoning_effort?: string;
    llm_max_input_tokens?: number;
  }): Promise<void> {
    setSaving(true);
    try {
      const updated = await api.settings.update(patch);
      modelsStore.applySettings(updated);
      settingsStore.set(updated);
    } catch (err) {
      console.warn("Failed to update model settings", err);
    } finally {
      setSaving(false);
    }
  }

  const presets = modelPresetMenuEntries(settings?.model_fallbacks?.[settings.llm_provider], false, [...models.map((model) => model.id), modelId]);
  const activePreset = matchingModelPreset(presets, {
    model: modelId, reasoning_effort: effort, context_tokens: settings?.llm_max_input_tokens,
  }, false);
  const catalogKnown = models.length > 0 && models.every((model) => !model.catalog_provider || model.catalog_provider === settings?.llm_provider);
  const modelGroups = [...presetModelGroups(presets, models, false, catalogKnown), ...groupModelsByPublisher(models)];
  const inCatalog = models.some((m) => m.id === modelId);
  const modelLabel = currentModel?.name ?? modelId ?? "Model";

  // Reasoning efforts advertised by the selected model (plus Auto). When the
  // model isn't reasoning-capable the picker is hidden entirely.
  const supportedEfforts = selectedModel?.supported_reasoning_efforts ?? [];
  const effortOptions: MenuOption[] = [
    { value: "", label: "Auto" },
    ...supportedEfforts.map((e) => ({ value: e, label: effortLabel(e) })),
  ];
  const effortLabelText =
    effort && supportedEfforts.includes(effort) ? effortLabel(effort) : "Auto";

  // Context size maps to the global llm_max_input_tokens budget; options adapt
  // to the selected model's advertised window.
  const ctxValue = settings?.llm_max_input_tokens ?? 0;
  const ctxOptions = contextOptions(selectedModel?.context_window);

  // Switching model carries over the reasoning effort + context size only if
  // they're still valid for the new model, otherwise they're reset/snapped so
  // we never send an unsupported value (e.g. a 936K budget to a 128K model).
  function onModelChange(nextId: string): void {
    const preset = presets.find((entry) => entry.value === nextId)?.preset;
    if (preset) {
      void save({
        llm_model: preset.model,
        llm_reasoning_effort: preset.reasoning_effort,
        llm_max_input_tokens: preset.context_tokens,
      });
      return;
    }
    const next = models.find((m) => m.id === nextId) ?? null;
    const patch: {
      llm_model: string;
      llm_reasoning_effort?: string;
      llm_max_input_tokens?: number;
    } = { llm_model: nextId };
    const nextEfforts = next?.supported_reasoning_efforts ?? [];
    if (effort && !nextEfforts.includes(effort)) patch.llm_reasoning_effort = "";
    if (ctxValue > 0) {
      const snapped = snapContextForModel(next?.context_window, ctxValue);
      if (snapped !== ctxValue) patch.llm_max_input_tokens = snapped;
    }
    void save(patch);
  }

  return (
    <div className="flex flex-wrap items-center gap-1 min-w-0">
      <ComposerSelectMenu
        ariaLabel="Model"
        tooltip="Model (applies to every conversation)"
        triggerLabel={modelLabel || "Select a model\u2026"}
        value={activePreset?.value ?? (inCatalog ? modelId : "")}
        groups={modelGroups}
        menuMinWidthClass="min-w-[18rem]"
        emptyHint="No model catalog — set one in Settings."
        disabled={saving}
        filterPlaceholder="Filter models…"
        onOpen={() => void modelsStore.ensureLoaded()}
        onSelect={onModelChange}
      />
      {supportedEfforts.length > 0 && (
        <ComposerSelectMenu
          ariaLabel="Reasoning effort"
          tooltip="Reasoning effort (supported by this model)"
          triggerLabel={effortLabelText}
          value={effort}
          groups={[{ options: effortOptions }]}
          disabled={saving}
          onSelect={(v) => void save({ llm_reasoning_effort: v })}
        />
      )}
      {ctxValue > 0 && (
        <ComposerSelectMenu
          ariaLabel="Context size"
          tooltip="Context size — max input tokens kept per turn"
          triggerLabel={`${formatTokens(ctxValue)} ctx`}
          value={String(ctxValue)}
          groups={[{ options: ctxOptions }]}
          disabled={saving}
          onSelect={(v) => void save({ llm_max_input_tokens: Number(v) })}
        />
      )}
    </div>
  );
}

function AgentModelControls() {
  const settings = useSettings();
  const [models, setModels] = useState<AgentModelInfo[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    api.agents.listModels()
      .then((m) => {
        if (alive) setModels(m);
      })
      .catch(() => {
        /* runtime may be unavailable — leave the list empty */
      });
    return () => {
      alive = false;
    };
  }, []);

  const defaultModel = settings?.agents_default_model ?? "";
  const effort = settings?.agents_reasoning_effort ?? "";
  const tier = settings?.agents_context_tier ?? "default";
  const selectedModel = models.find((m) => m.id === defaultModel) ?? null;

  async function save(patch: {
    agents_default_model?: string;
    agents_reasoning_effort?: string;
    agents_context_tier?: string;
  }): Promise<void> {
    setSaving(true);
    try {
      const updated = await api.settings.update(patch);
      settingsStore.set(updated);
    } catch (err) {
      console.warn("Failed to update agent settings", err);
    } finally {
      setSaving(false);
    }
  }

  const options: MenuOption[] = [
    { value: "", label: "Runtime default" },
    // Keep the saved value selectable even if the runtime no longer lists it.
    ...(defaultModel && !models.some((m) => m.id === defaultModel)
      ? [{ value: defaultModel, label: defaultModel }]
      : []),
    ...models.map((m) => ({ value: m.id, label: m.name })),
  ];
  const label = defaultModel
    ? (models.find((m) => m.id === defaultModel)?.name ?? defaultModel)
    : "Runtime default";
  const presets = modelPresetMenuEntries(settings?.model_fallbacks?.agents, true, [...models.map((model) => model.id), defaultModel]);
  const activePreset = matchingModelPreset(presets, {
    model: defaultModel, reasoning_effort: effort, context_tier: tier,
  }, true);
  const modelGroups = [...presetModelGroups(presets, models, true, models.length > 0), { options }];

  // Reasoning efforts advertised by the selected agent model (plus Auto). The
  // "Runtime default" model has no catalog entry, so the picker stays hidden
  // until a concrete model is chosen.
  const supportedEfforts = selectedModel?.supported_reasoning_efforts ?? [];
  const effortOptions: MenuOption[] = [
    { value: "", label: "Auto" },
    ...supportedEfforts.map((e) => ({ value: e, label: effortLabel(e) })),
  ];
  const effortLabelText =
    effort && supportedEfforts.includes(effort) ? effortLabel(effort) : "Auto";

  // Agents expose a context *tier* (default vs long context), not a token budget.
  const tierOptions: MenuOption[] = [
    { value: "default", label: "Default context" },
    { value: "long_context", label: "Long context" },
  ];
  const tierLabelText = tier === "long_context" ? "Long ctx" : "Default ctx";

  // Switching model drops a reasoning effort the new model doesn't support.
  function onModelChange(nextId: string): void {
    const preset = presets.find((entry) => entry.value === nextId)?.preset;
    if (preset) {
      void save({
        agents_default_model: preset.model,
        agents_reasoning_effort: preset.reasoning_effort,
        agents_context_tier: preset.context_tier,
      });
      return;
    }
    const next = models.find((m) => m.id === nextId) ?? null;
    const patch: { agents_default_model: string; agents_reasoning_effort?: string } = {
      agents_default_model: nextId,
    };
    const nextEfforts = next?.supported_reasoning_efforts ?? [];
    if (effort && !nextEfforts.includes(effort)) patch.agents_reasoning_effort = "";
    void save(patch);
  }

  return (
    <div className="flex flex-wrap items-center gap-1 min-w-0">
      <ComposerSelectMenu
        ariaLabel="Agent model"
        tooltip="Default model for new agent sessions"
        triggerLabel={label}
        value={activePreset?.value ?? defaultModel}
        groups={modelGroups}
        menuMinWidthClass="min-w-[18rem]"
        disabled={saving}
        filterPlaceholder="Filter models…"
        onSelect={onModelChange}
      />
      {supportedEfforts.length > 0 && (
        <ComposerSelectMenu
          ariaLabel="Reasoning effort"
          tooltip="Reasoning effort (supported by this model)"
          triggerLabel={effortLabelText}
          value={effort}
          groups={[{ options: effortOptions }]}
          disabled={saving}
          onSelect={(v) => void save({ agents_reasoning_effort: v })}
        />
      )}
      <ComposerSelectMenu
        ariaLabel="Context tier"
        tooltip="Context window tier for new agent sessions"
        triggerLabel={tierLabelText}
        value={tier}
        groups={[{ options: tierOptions }]}
        disabled={saving}
        onSelect={(v) => void save({ agents_context_tier: v })}
      />
    </div>
  );
}
