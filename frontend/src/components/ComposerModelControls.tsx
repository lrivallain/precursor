import { useEffect, useState } from "react";
import { Brain, Cpu, Gauge } from "lucide-react";
import { api } from "../lib/api";
import { modelsStore, useModelsVersion } from "../lib/modelsStore";
import { settingsStore, useSettings } from "../lib/settingsStore";
import type { AgentModelInfo, ConversationLLMSelection, LLMModel, LLMProviderSpec, ModelCategory } from "../lib/types";
import { selectedModelCategoryEntry, modelPresetMenuEntries, providerModelMenuValue, categoryLabel, type ModelPresetMenuEntry } from "../lib/modelPresetMenus";
import { openModelPresetSettings } from "../lib/modelPresetSettingsOpen";
import { providerSetupIssue } from "../lib/modelPresetHealth";
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

function presetModelGroups(entries: ModelPresetMenuEntry[], models: { id: string; name: string }[]): MenuGroup[] {
  if (!entries.length) return [];
  return [{
    parentLabel: "Presets",
    options: entries.map(({ value, label, presets }) => {
      return {
        value,
        label,
        description: `Engine chooses from ${presets.length} ordered ${presets.length === 1 ? "preset" : "presets"}${new Set(presets.map((preset) => preset.provider)).size > 1 ? " across providers" : ""}`,
        searchText: presets.map((preset) => `${preset.model} ${models.find((model) => model.id === preset.model)?.name ?? ""}`).join(" "),
      };
    }),
  }];
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
 * composer toolbar. Chats/topics persist their own LLM selection; other `llm`
 * surfaces keep writing global defaults. The `agents` variant writes the SDK
 * agents' default model (Settings → Agents). Kept self-contained — it
 * reads/writes the shared stores directly so the dumb `Composer` doesn't need
 * new props.
 */
export function ComposerModelControls({
  variant = "llm",
  conversation,
}: {
  variant?: "llm" | "agents";
  conversation?: {
    kind: "topic" | "chat";
    id: number;
    selection: ConversationLLMSelection | null;
    onUpdated: () => void;
  };
}) {
  if (variant === "agents") return <AgentModelControls />;
  return <LlmModelControls key={conversation ? `${conversation.kind}:${conversation.id}` : "global"} conversation={conversation} />;
}

function LlmModelControls({ conversation }: { conversation?: Parameters<typeof ComposerModelControls>[0]["conversation"] }) {
  useModelsVersion();
  const settings = useSettings();
  const [saving, setSaving] = useState(false);
  const [selection, setSelection] = useState(conversation?.selection ?? null);
  const [providers, setProviders] = useState<LLMProviderSpec[]>([]);
  const [error, setError] = useState<string | null>(null);
  const providerId = selection?.provider || settings?.llm_provider || "github_copilot";
  const currentProvider = providers.find((entry) => entry.id === providerId);
  const configuredProviders = settings ? providers.filter((provider) => !providerSetupIssue(settings, provider)) : [];

  useEffect(() => {
    setSelection(conversation?.selection ?? null);
  }, [conversation?.selection]);
  useEffect(() => {
    void modelsStore.ensureLoaded(providerId);
  }, [providerId]);
  useEffect(() => {
    if (conversation) for (const provider of configuredProviders) void modelsStore.ensureLoaded(provider.id);
  }, [!!conversation, providers, settings]);
  useEffect(() => {
    modelsStore.applySettings(settings);
  }, [settings]);
  useEffect(() => {
    if (!conversation) return;
    let alive = true;
    api.llm.listProviders().then((list) => { if (alive) setProviders(list); })
      .catch((err: unknown) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [!!conversation]);

  const models = modelsStore.all(providerId);
  const effort = selection?.reasoning_effort ?? settings?.llm_reasoning_effort ?? "";
  const category = selection ? selection.category : settings?.llm_model_category ?? null;
  const modelId = selection?.model || (category
    ? settings?.model_fallbacks?.categories.find((entry) => entry.id === category)?.presets.find((preset) => preset.provider !== "agents")?.model
    : settings?.llm_model) || "";
  const selectedModel = models.find((m) => m.id === modelId) ?? null;
  const ctxValue = selection?.context_tokens ?? settings?.llm_max_input_tokens ?? 128_000;

  async function saveSelection(next: ConversationLLMSelection | null): Promise<void> {
    if (!conversation) return;
    setSaving(true);
    setError(null);
    try {
      const updated = conversation.kind === "topic"
        ? await api.topics.update(conversation.id, { llm_selection: next })
        : await api.chats.update(conversation.id, { llm_selection: next });
      setSelection(updated.llm_selection);
      conversation.onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function save(patch: {
    llm_model?: string;
    llm_model_category?: ModelCategory | null;
    llm_reasoning_effort?: string;
    llm_max_input_tokens?: number;
  }, nextProvider = providerId): Promise<void> {
    if (conversation) {
      const nextCategory = patch.llm_model_category !== undefined ? patch.llm_model_category : category;
      const nextEffort = patch.llm_reasoning_effort ?? effort;
      if (!["", "minimal", "low", "medium", "high", "xhigh", "max"].includes(nextEffort)) {
        setError(`Unsupported reasoning effort: ${nextEffort}`);
        return;
      }
      await saveSelection({
        provider: nextProvider,
        model: nextCategory ? "" : patch.llm_model ?? modelId,
        category: nextCategory,
        reasoning_effort: nextEffort as ConversationLLMSelection["reasoning_effort"],
        context_tokens: patch.llm_max_input_tokens ?? ctxValue,
      });
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await api.settings.update(patch);
      modelsStore.applySettings(updated);
      settingsStore.set(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      console.warn("Failed to update model settings", err);
    } finally {
      setSaving(false);
    }
  }

  const presets = modelPresetMenuEntries(settings?.model_fallbacks, false, [...models.map((model) => model.id), modelId]);
  const activePreset = selectedModelCategoryEntry(presets, category);
  const menuSelections = new Map<string, { provider: string; model: string; category: ModelCategory | null }>();
  function modelValue(provider: string, model: string, category: ModelCategory | null = null): string {
    const value = providerModelMenuValue(provider, category ? "preset" : "model", category ?? model);
    menuSelections.set(value, { provider, model, category });
    return value;
  }
  const allCatalogModels = configuredProviders.flatMap((provider) => modelsStore.all(provider.id));
  const modelGroups: MenuGroup[] = conversation ? [
    { options: [{ value: "", label: "Use global default" }] },
    ...presetModelGroups(presets, allCatalogModels).map((group) => ({
      ...group,
      options: group.options.map((option, index) => ({
        ...option, value: modelValue("", "", presets[index].category),
      })),
    })),
    ...configuredProviders.flatMap((provider): MenuGroup[] => {
      const catalog = modelsStore.all(provider.id);
      const groups: MenuGroup[] = groupModelsByPublisher(catalog).map((group) => ({
        ...group, parentLabel: provider.label,
        options: group.options.map((option) => ({
          ...option, value: modelValue(provider.id, option.value), searchText: option.value,
        })),
      }));
      if (!groups.length) groups.push({
        parentLabel: provider.label,
        options: [{
          value: `${provider.id}:catalog-status`, disabled: true,
          label: modelsStore.error(provider.id) ? "Models unavailable" : modelsStore.hasLoaded(provider.id) ? "No models available" : "Loading models...",
          description: modelsStore.error(provider.id) ?? (modelsStore.hasLoaded(provider.id) ? "Configure providers in Settings > Model." : "Fetching model catalogue..."),
        }],
      });
      return groups;
    }),
  ] : [...presetModelGroups(presets, models), ...groupModelsByPublisher(models)];
  const inCatalog = models.some((m) => m.id === modelId);
  const modelLabel = category
    ? categoryLabel(settings?.model_fallbacks, category)
    : selectedModel?.name ?? modelId ?? "Model";

  // Reasoning efforts advertised by the selected model (plus Auto). When the
  // model isn't reasoning-capable the picker is hidden entirely.
  const supportedEfforts = selectedModel?.supported_reasoning_efforts ?? [];
  const effortOptions: MenuOption[] = [
    { value: "", label: "Auto" },
    ...supportedEfforts.map((e) => ({ value: e, label: effortLabel(e) })),
  ];
  const effortLabelText =
    effort && supportedEfforts.includes(effort) ? effortLabel(effort) : "Auto";

  // Budget options adapt to the selected model's advertised window.
  const ctxOptions = contextOptions(selectedModel?.context_window);

  // Switching model carries over the reasoning effort + context size only if
  // they're still valid for the new model, otherwise they're reset/snapped so
  // we never send an unsupported value (e.g. a 936K budget to a 128K model).
  function onModelChange(value: string): void {
    if (conversation && !value) { void saveSelection(null); return; }
    const chosen = conversation ? menuSelections.get(value) : undefined;
    if (conversation && !chosen) { setError("This model selection is no longer available. Reopen the model menu."); return; }
    const nextProvider = chosen?.provider ?? providerId;
    const nextId = chosen?.model ?? value;
    const preset = conversation ? undefined : presets.find((entry) => entry.value === nextId);
    if (chosen?.category) {
      void save({ llm_model_category: chosen.category }, "");
      return;
    }
    if (preset) {
      void save({ llm_model_category: preset.category });
      return;
    }
    const next = modelsStore.byIdOrNull(nextId, nextProvider);
    const patch: {
      llm_model: string;
      llm_model_category: null;
      llm_reasoning_effort?: string;
      llm_max_input_tokens?: number;
    } = { llm_model: nextId, llm_model_category: null };
    const nextEfforts = next?.supported_reasoning_efforts ?? [];
    if (effort && !nextEfforts.includes(effort)) patch.llm_reasoning_effort = "";
    if (ctxValue > 0) {
      const snapped = snapContextForModel(next?.context_window, ctxValue);
      if (snapped !== ctxValue) patch.llm_max_input_tokens = snapped;
    }
    void save(patch, nextProvider);
  }

  return (
    <div className="flex flex-wrap items-center gap-1 min-w-0">
      <ComposerSelectMenu
        ariaLabel="Model"
        compactIcon={<Cpu size={14} className="shrink-0" />}
        tooltip={conversation ? `Model for this conversation only\nProvider: ${currentProvider?.label ?? providerId}` : "Model (global default)"}
        triggerLabel={`${modelLabel || "Select a model\u2026"}${conversation && !selection ? " · Default" : ""}`}
        value={conversation ? selection ? modelValue(providerId, selection.model, selection.category) : "" : activePreset?.value ?? (inCatalog ? modelId : "")}
        groups={modelGroups}
        menuMinWidthClass="min-w-[18rem]"
        emptyHint="No model catalog — set one in Settings."
        disabled={saving}
        filterPlaceholder="Filter models…"
        scrollToSelected={!conversation && presets.length === 0}
        footerAction={{ label: "Manage presets in Settings...", onSelect: openModelPresetSettings }}
        onOpen={() => {
          if (conversation) for (const provider of configuredProviders) void modelsStore.ensureLoaded(provider.id);
          else void modelsStore.ensureLoaded(providerId);
        }}
        onSelect={onModelChange}
        onCustomValue={conversation ? undefined : onModelChange}
        customOptions={conversation ? (query) => configuredProviders
          .filter((provider) => !modelsStore.all(provider.id).some((model) => model.id === query))
          .map((provider) => ({
            value: modelValue(provider.id, query),
            label: `Use model id "${query}"`,
            description: provider.label,
          })) : undefined}
      />
      {category && !activePreset && <span className="text-[11px] text-amber-700 dark:text-amber-400" data-tooltip="This category has no configured presets. Choose a direct model or configure it in Settings.">Category needs presets</span>}
      {!category && supportedEfforts.length > 0 && (
        <ComposerSelectMenu
          ariaLabel="Reasoning effort"
          compactIcon={<Brain size={14} className="shrink-0" />}
          tooltip="Reasoning effort (supported by this model)"
          triggerLabel={effortLabelText}
          value={effort}
          groups={[{ options: effortOptions }]}
          disabled={saving}
          onSelect={(v) => void save({ llm_reasoning_effort: v })}
        />
      )}
      {!category && ctxValue > 0 && (
        <ComposerSelectMenu
          ariaLabel="Context size"
          compactIcon={<Gauge size={14} className="shrink-0" />}
          tooltip="Context size — max input tokens kept per turn"
          triggerLabel={`${formatTokens(ctxValue)} ctx`}
          value={String(ctxValue)}
          groups={[{ options: ctxOptions }]}
          disabled={saving}
          onSelect={(v) => void save({ llm_max_input_tokens: Number(v) })}
        />
      )}
      {(error || (conversation && modelsStore.error(providerId))) && (
        <span role="alert" className="text-[11px] text-red-600 dark:text-red-400">
          {error || modelsStore.error(providerId)} Configure providers in Settings &gt; Model.
        </span>
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
  const category = settings?.agents_model_category ?? null;
  const tier = settings?.agents_context_tier ?? "default";
  const selectedModel = models.find((m) => m.id === defaultModel) ?? null;

  async function save(patch: {
    agents_default_model?: string;
    agents_model_category?: ModelCategory | null;
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
  const label = category
    ? categoryLabel(settings?.model_fallbacks, category)
    : defaultModel
    ? (models.find((m) => m.id === defaultModel)?.name ?? defaultModel)
    : "Runtime default";
  const presets = modelPresetMenuEntries(settings?.model_fallbacks, true, [...models.map((model) => model.id), defaultModel]);
  const activePreset = selectedModelCategoryEntry(presets, category);
  const modelGroups = [...presetModelGroups(presets, models), { options }];

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
    const preset = presets.find((entry) => entry.value === nextId);
    if (preset) {
      void save({ agents_model_category: preset.category });
      return;
    }
    const next = models.find((m) => m.id === nextId) ?? null;
    const patch: { agents_default_model: string; agents_model_category: null; agents_reasoning_effort?: string } = {
      agents_default_model: nextId,
      agents_model_category: null,
    };
    const nextEfforts = next?.supported_reasoning_efforts ?? [];
    if (effort && !nextEfforts.includes(effort)) patch.agents_reasoning_effort = "";
    void save(patch);
  }

  return (
    <div className="flex flex-wrap items-center gap-1 min-w-0">
      <ComposerSelectMenu
        ariaLabel="Agent model"
        compactIcon={<Cpu size={14} className="shrink-0" />}
        tooltip="Default model for new agent sessions"
        triggerLabel={label}
        value={activePreset?.value ?? defaultModel}
        groups={modelGroups}
        menuMinWidthClass="min-w-[18rem]"
        disabled={saving}
        filterPlaceholder="Filter models…"
        scrollToSelected={presets.length === 0}
        footerAction={{ label: "Manage presets in Settings...", onSelect: openModelPresetSettings }}
        onSelect={onModelChange}
      />
      {category && <span className={`text-[11px] ${activePreset ? "text-muted" : "text-amber-700 dark:text-amber-400"}`} data-tooltip={activePreset ? "The engine chooses model, effort and context for unpinned agent runs from this category" : "This category has no configured presets. Choose a direct model or configure it in Settings."}>{activePreset ? "Engine selects model" : "Category needs presets"}</span>}
      {!category && supportedEfforts.length > 0 && (
        <ComposerSelectMenu
          ariaLabel="Reasoning effort"
          compactIcon={<Brain size={14} className="shrink-0" />}
          tooltip="Reasoning effort (supported by this model)"
          triggerLabel={effortLabelText}
          value={effort}
          groups={[{ options: effortOptions }]}
          disabled={saving}
          onSelect={(v) => void save({ agents_reasoning_effort: v })}
        />
      )}
      {!category && <ComposerSelectMenu
        ariaLabel="Context tier"
        compactIcon={<Gauge size={14} className="shrink-0" />}
        tooltip="Context window tier for new agent sessions"
        triggerLabel={tierLabelText}
        value={tier}
        groups={[{ options: tierOptions }]}
        disabled={saving}
        onSelect={(v) => void save({ agents_context_tier: v })}
      />}
    </div>
  );
}
