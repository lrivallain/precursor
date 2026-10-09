import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, Bot, CheckCircle2, CircleHelp, LoaderCircle, MessageSquare, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { AgentModelInfo, LLMModel, LLMProviderSpec, ModelCategories, ModelCategory, ModelPreset, Settings } from "../lib/types";
import { Select } from "./Select";
import { ComposerSelectMenu, type MenuGroup } from "./ComposerSelectMenu";
import { groupModelsByPublisher } from "./ComposerModelControls";
import { useSettings } from "../lib/settingsStore";
import { checkPresets, providerSetupIssue, summarizePresets, type CatalogCheck, type PresetSummary } from "../lib/modelPresetHealth";

const CATEGORIES: { id: ModelCategory; label: string; hint: string }[] = [
  { id: "efficiency", label: "Efficiency", hint: "Fast, economical tasks" },
  { id: "balanced", label: "Balanced", hint: "Everyday work" },
  { id: "intelligence", label: "Intelligence", hint: "Complex reasoning" },
];
const EMPTY: ModelCategories = { efficiency: [], balanced: [], intelligence: [] };
const EFFORTS: ModelPreset["reasoning_effort"][] = ["", "minimal", "low", "medium", "high", "xhigh", "max"];
const INPUT_CLASS = "w-full min-w-0 bg-bg border border-border rounded px-2 py-1.5 text-xs outline-none focus:border-accent";

function presetCount(categories: ModelCategories | undefined): number {
  return Object.values(categories ?? EMPTY).reduce((count, presets) => count + presets.length, 0);
}

function HealthBadge({ state, label, detail }: Pick<PresetSummary, "state" | "label" | "detail">) {
  const warning = state === "review" || state === "setup" || state === "unverified";
  const Icon = state === "valid" ? CheckCircle2 : warning ? AlertTriangle : state === "checking" ? LoaderCircle : CircleHelp;
  return (
    <span
      data-tooltip={detail}
      className={`inline-flex items-center gap-1 text-[11px] ${state === "valid" ? "text-emerald-700 dark:text-emerald-400" : warning ? "text-amber-700 dark:text-amber-400" : "text-muted"}`}
    >
      <Icon size={12} aria-hidden="true" className={`shrink-0 ${state === "checking" ? "animate-spin" : ""}`} />
      {label}
    </span>
  );
}

export function ModelFallbackSettings({
  value, onChange, settings, provider, providers, models, modelsProvider, modelsLoading, modelsError, onCheckModels,
}: {
  value: Record<string, ModelCategories>;
  onChange: (value: Record<string, ModelCategories>) => void;
  settings: Settings | null;
  provider: string;
  providers: LLMProviderSpec[];
  models: LLMModel[];
  modelsProvider: string | null;
  modelsLoading: boolean;
  modelsError: string | null;
  onCheckModels: () => Promise<LLMModel[]>;
}) {
  const effectiveSettings = useSettings() ?? settings;
  const [scope, setScope] = useState<"provider" | "agents">("provider");
  const [agentModels, setAgentModels] = useState<AgentModelInfo[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [agentCatalogState, setAgentCatalogState] = useState<CatalogCheck["state"]>("unchecked");
  const [checking, setChecking] = useState(false);
  const agentRequest = useRef(0);
  const editorId = useId();
  const editorHeadingId = useId();
  const agents = scope === "agents";
  const scopeId = agents ? "agents" : provider;
  const categories = value[scopeId] ?? EMPTY;
  const providerSpec = providers.find((entry) => entry.id === provider);
  const setupIssue = providerSetupIssue(effectiveSettings, providerSpec);
  const agentReady = Boolean(effectiveSettings?.agents_enabled && effectiveSettings.agents_available && effectiveSettings.agents_runtime_started);
  const chatCheck: CatalogCheck = setupIssue
    ? { state: "setup", detail: setupIssue, models: [] }
    : !providerSpec || modelsProvider !== provider
      ? { state: "unchecked", detail: "Refresh the selected provider's catalogue to check these presets.", models: [] }
      : modelsLoading
        ? { state: "checking", detail: "Reading the selected provider's model catalogue.", models: [] }
        : modelsError
          ? { state: "unavailable", detail: `Catalogue unavailable: ${modelsError}. Check provider settings and retry.`, models: [] }
          : models.some((model) => model.catalog_provider && model.catalog_provider !== provider)
            ? { state: "setup", detail: "The selected provider resolved to a different catalogue source (such as the offline mock). Review its credentials and configuration, then Apply & refresh models.", models: [] }
            : !providerSpec.discovers_models
              ? { state: "unsupported", detail: `${providerSpec.label} does not publish a model catalogue. Deployment ids need manual review.`, models }
            : !models.length
              ? { state: "unavailable", detail: "No model catalogue was returned. Check provider access and retry.", models: [] }
              : { state: "ready", detail: "Checked against the selected provider's catalogue.", models };
  const agentCheck: CatalogCheck = !agentReady
    ? { state: "setup", detail: "Enable and start the Copilot SDK runtime in Settings > Agents to check agent model ids.", models: [] }
    : { state: agentCatalogState, detail: catalogError ?? (agentCatalogState === "ready" ? "Checked against the Copilot SDK model catalogue." : "Read the Copilot SDK model catalogue to verify these presets."), models: agentModels };
  const activeCheck = agents ? agentCheck : chatCheck;
  const catalog = agents ? (agentReady ? agentModels : []) : (chatCheck.state === "setup" || modelsProvider !== provider ? [] : models);
  const chatHealth = checkPresets(value[provider] ?? EMPTY, chatCheck, false);
  const agentHealth = checkPresets(value.agents ?? EMPTY, agentCheck, true);
  const categoryChecks = agents ? agentHealth : chatHealth;
  const offered = catalog.filter((model) => model.id !== "auto");
  const modelGroups: MenuGroup[] = agents
    ? [{ options: offered.map((model) => ({ value: model.id, label: model.name })) }]
    : groupModelsByPublisher(models.filter((model) => model.id !== "auto" && catalog.some((entry) => entry.id === model.id)));
  const savedIds = [...new Set(Object.values(categories).flat().map((preset) => preset.model))]
    .filter((model) => model && !offered.some((entry) => entry.id === model));
  if (savedIds.length) modelGroups.unshift({
    label: "Saved model ids",
    options: savedIds.map((model) => ({ value: model, label: model, description: activeCheck.state === "ready" ? "Not in the current catalog" : "Catalog not checked" })),
  });

  const loadAgentModels = useCallback(async (): Promise<void> => {
    const request = ++agentRequest.current;
    setAgentCatalogState("checking");
    setCatalogError(null);
    setAgentModels([]);
    try {
      const list = await api.agents.listModels();
      if (request !== agentRequest.current) return;
      setAgentModels(list);
      setAgentCatalogState(list.length ? "ready" : "unavailable");
      if (!list.length) setCatalogError("The SDK returned no model catalogue. Check the runtime in Settings > Agents and retry.");
    } catch (error: unknown) {
      if (request !== agentRequest.current) return;
      setAgentCatalogState("unavailable");
      setCatalogError(`Agent catalogue unavailable: ${error instanceof Error ? error.message : String(error)}. Check the runtime and retry.`);
    }
  }, []);

  useEffect(() => {
    if (agentReady) void loadAgentModels();
    return () => { agentRequest.current++; };
  }, [agentReady, loadAgentModels]);

  async function refreshChecks(): Promise<void> {
    setChecking(true);
    try {
      await Promise.all([onCheckModels(), agentReady ? loadAgentModels() : Promise.resolve()]);
    } finally {
      setChecking(false);
    }
  }

  function replace(category: ModelCategory, presets: ModelPreset[]): void {
    onChange({ ...value, [scopeId]: { ...categories, [category]: presets } });
  }

  function update(category: ModelCategory, index: number, patch: Partial<ModelPreset>): void {
    replace(category, categories[category].map((preset, i) => i === index ? { ...preset, ...patch } : preset));
  }

  function add(category: ModelCategory, current = false): void {
    const selected = agents ? effectiveSettings?.agents_default_model : effectiveSettings?.llm_model;
    const assigned = new Set(Object.values(categories).flat().map((p) => p.model));
    const model = current && selected && selected !== "auto" ? selected : catalog.find((m) => m.id !== "auto" && !assigned.has(m.id))?.id ?? "";
    const storedEffort = agents ? effectiveSettings?.agents_reasoning_effort : effectiveSettings?.llm_reasoning_effort;
    const effort = current ? EFFORTS.find((e) => e === storedEffort) ?? "" : "";
    replace(category, [...categories[category], {
      model,
      reasoning_effort: effort,
      context_tokens: current ? effectiveSettings?.llm_max_input_tokens ?? 128_000 : 128_000,
      context_tier: agents && current && effectiveSettings?.agents_context_tier === "long_context" ? "long_context" : "default",
    }]);
  }

  function move(category: ModelCategory, index: number, delta: number): void {
    const presets = [...categories[category]];
    [presets[index], presets[index + delta]] = [presets[index + delta], presets[index]];
    replace(category, presets);
  }

  const currentModel = agents ? effectiveSettings?.agents_default_model : effectiveSettings?.llm_model;
  const providerLabel = providers.find((p) => p.id === provider)?.label ?? provider;
  const configurations = [
    { id: "provider" as const, label: "Chat & live", runtime: providerLabel, icon: MessageSquare,
      description: "Topics, chats, live analysis and summaries", count: presetCount(value[provider]),
      health: summarizePresets(Object.values(chatHealth).flat(), chatCheck),
      ariaLabel: "Configure chat and live model alternatives" },
    { id: "agents" as const, label: "Agents & workflows", runtime: "Copilot SDK", icon: Bot,
      description: "Agent tasks and workflow steps", count: presetCount(value.agents),
      health: summarizePresets(Object.values(agentHealth).flat(), agentCheck),
      ariaLabel: "Configure agent and workflow model alternatives" },
  ];
  return (
    <div className="@container mt-6 space-y-4 break-words" data-model-fallback-settings>
      <h3 className="text-sm font-medium">Model alternatives</h3>
      <p className="text-xs text-muted">
        Chat and agent tasks use <strong className="font-medium text-text">two separate configurations</strong>.
        {" "}To enable alternatives for both, configure both below. You can leave either
        empty if you don't use it.
      </p>
      <div className="grid grid-cols-1 @sm:grid-cols-2 gap-3" role="group" aria-label="Independent model alternative configurations">
        {configurations.map(({ id, label, runtime, icon: Icon, description, count, health, ariaLabel }) => (
          <button
            key={id}
            type="button"
            aria-label={ariaLabel}
            aria-pressed={scope === id}
            aria-controls={editorId}
            aria-describedby={`${editorId}-${id}-health`}
            data-model-fallback-scope={id}
            onClick={() => setScope(id)}
            className={`min-w-0 rounded border p-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg ${
              scope === id ? "border-accent bg-surface" : "border-border hover:border-accent/50 hover:bg-surface"
            }`}
          >
            <span className="flex items-center gap-2 text-sm font-medium">
              <Icon size={16} className="shrink-0" aria-hidden="true" /><span className="min-w-0">{label}</span>
            </span>
            <span className="mt-1 block text-xs text-muted">{runtime}</span>
            <span className="mt-2 block text-[11px] text-muted">{description}</span>
            <span id={`${editorId}-${id}-health`} className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs">
              <span className={count ? "text-text" : "text-muted"}>{count ? `${count} ${count === 1 ? "preset" : "presets"}` : "Not configured"}</span>
              <span className="text-accent">{scope === id ? "Editing presets" : "Edit presets"}</span>
              {count > 0 && <span className="basis-full" data-scope-health={health.state}><HealthBadge {...health} /></span>}
            </span>
          </button>
        ))}
      </div>
      <p className="text-[11px] text-muted">Presets are not shared, even with the same Copilot subscription. Save applies both configurations.</p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void refreshChecks()} disabled={checking || modelsLoading || (agentReady && agentCatalogState === "checking") || !provider} className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-xs hover:bg-surface disabled:opacity-50">
          <RefreshCw size={12} aria-hidden="true" className={checking ? "animate-spin" : ""} />
          {checking ? "Checking models..." : "Check models"}
        </button>
        <span className="text-[11px] text-muted">Catalogue checks only; no inference requests.</span>
      </div>
      <div id={editorId} role="region" aria-labelledby={editorHeadingId} className="space-y-3 pt-2">
        <div className="border-b border-border pb-3">
          <h4 id={editorHeadingId} className="text-sm font-medium">{agents ? "Agent & workflow presets" : "Chat & live presets"}</h4>
          <p className="mt-1 text-[11px] text-muted">
            {agents ? "Copilot SDK - effort and context tier." : `${providerLabel} - effort and context-token budget.`}
            {" "}Add your selected model and its alternatives to the same category; retries follow the order below.
          </p>
        </div>
      {activeCheck.state !== "ready" && activeCheck.state !== "checking" && (
        <p role="status" className="text-xs text-amber-700 dark:text-amber-400">{activeCheck.detail} Presets are kept, but their models are not verified.</p>
      )}
      {agents && currentModel === "auto" && <p className="text-[11px] text-muted">The SDK selection is Auto. Choose an explicit agent model to categorise it; Auto keeps the runtime's own routing.</p>}
      {CATEGORIES.map(({ id, label, hint }) => {
        const health = summarizePresets(categoryChecks[id], activeCheck);
        return (
        <div key={id} className="rounded border border-border p-3 space-y-2" data-model-fallback-category data-category-health={health.state}>
          <div className="flex flex-col gap-1 @sm:flex-row @sm:items-center @sm:justify-between @sm:gap-2">
            <div><h5 className="text-xs font-medium">{label}</h5><p className="text-[11px] text-muted">{hint}</p></div>
            <div className="flex flex-wrap items-center gap-2">
              {categories[id].length > 0 && <span className="text-[11px] text-muted">{categories[id].length} {categories[id].length === 1 ? "preset" : "presets"}{activeCheck.state === "ready" && health.state !== "valid" ? ` · ${health.listed}/${health.total} listed` : ""}</span>}
              <HealthBadge {...health} />
            </div>
          </div>
          {activeCheck.state === "ready" && health.total > 0 && health.listed === 0 && (
            <p className="text-[11px] text-amber-700 dark:text-amber-400">No listed alternatives remain in this category. Add a currently listed model.</p>
          )}
          {categories[id].map((preset, index) => (
            <div key={index} className="space-y-2 rounded bg-surface p-2" data-preset-health={categoryChecks[id][index].issues.length ? "review" : categoryChecks[id][index].listed ? "valid" : "unverified"}>
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-[11px] text-muted w-4 shrink-0">{index + 1}.</span>
                <div className="flex-1 min-w-0 basis-24">
                  <ComposerSelectMenu
                    ariaLabel={`${label} model ${index + 1}`}
                    tooltip={preset.model ? `Model: ${preset.model}` : "Select a model for this preset"}
                    triggerLabel={catalog.find((model) => model.id === preset.model)?.name ?? (preset.model || "Select a model...")}
                    value={preset.model}
                    groups={modelGroups}
                    disabled={false}
                    fullWidth
                    portal
                    filterPlaceholder="Filter models…"
                    emptyHint="No catalog - enter a model id in the search."
                    onSelect={(model) => update(id, index, { model })}
                    onCustomValue={(model) => update(id, index, { model })}
                  />
                </div>
                <button type="button" aria-label={`Move ${label} preset ${index + 1} up`} data-tooltip="Try earlier" disabled={index === 0} onClick={() => move(id, index, -1)} className="p-1 rounded hover:bg-bg disabled:opacity-30"><ArrowUp size={13} /></button>
                <button type="button" aria-label={`Move ${label} preset ${index + 1} down`} data-tooltip="Try later" disabled={index === categories[id].length - 1} onClick={() => move(id, index, 1)} className="p-1 rounded hover:bg-bg disabled:opacity-30"><ArrowDown size={13} /></button>
                <button type="button" aria-label={`Remove ${label} preset ${index + 1}`} data-tooltip="Remove preset" onClick={() => replace(id, categories[id].filter((_, i) => i !== index))} className="p-1 rounded hover:bg-bg"><Trash2 size={13} /></button>
              </div>
              <div className="grid grid-cols-1 @sm:grid-cols-2 gap-2">
                <div>
                  <label className="block text-[11px] text-muted mb-1">Effort</label>
                  <Select value={preset.reasoning_effort} onChange={(v) => {
                    const effort = EFFORTS.find((e) => e === v);
                    if (effort !== undefined) update(id, index, { reasoning_effort: effort });
                  }} options={EFFORTS.map((e) => ({ value: e, label: e || "Auto (omit effort)" }))} ariaLabel={`${label} effort ${index + 1}`} fullWidth size="sm" />
                </div>
                <div>
                  <label className="block text-[11px] text-muted mb-1">{agents ? "Context tier" : "Context budget (tokens)"}</label>
                  {agents ? (
                    <Select value={preset.context_tier} onChange={(v) => {
                      if (v === "default" || v === "long_context") update(id, index, { context_tier: v });
                    }} options={[{ value: "default", label: "Default" }, { value: "long_context", label: "Long context" }]} ariaLabel={`${label} context ${index + 1}`} fullWidth size="sm" />
                  ) : (
                    <input className={INPUT_CLASS} type="number" min={1000} max={5000000} step={1000} aria-label={`${label} context ${index + 1}`} value={preset.context_tokens} onChange={(e) => update(id, index, { context_tokens: Number(e.target.value) })} />
                  )}
                </div>
              </div>
              <HealthBadge
                state={categoryChecks[id][index].issues.length ? "review" : categoryChecks[id][index].listed ? "valid" : "unverified"}
                label={categoryChecks[id][index].issues.length ? "Review needed" : categoryChecks[id][index].listed ? "Model listed" : "Model not checked"}
                detail={categoryChecks[id][index].issues.join("\n") || activeCheck.detail}
              />
              {categoryChecks[id][index].issues.length > 0 && (
                <ul className="space-y-1 text-[11px] text-amber-700 dark:text-amber-400">
                  {categoryChecks[id][index].issues.map((issue) => <li key={issue}>{issue}</li>)}
                </ul>
              )}
            </div>
          ))}
          <div className="flex flex-wrap gap-3">
            <button type="button" onClick={() => add(id)} disabled={categories[id].length >= 12} className="flex items-center gap-1 text-xs text-accent disabled:opacity-40"><Plus size={13} />Add preset</button>
            <button type="button" onClick={() => add(id, true)} disabled={!currentModel || currentModel === "auto" || categories[id].length >= 12} className="text-xs text-accent disabled:opacity-40">Add current selection</button>
          </div>
        </div>
      ); })}
      <p className="text-[11px] text-muted">
        Keep retired ids here so Precursor can still identify their category.
        If a model is in multiple categories, its effort and context must match one
        unambiguously. Alternatives never cross providers or change your saved selection.
        Retries stop once output or agent actions begin.
        {" "}Listed model ids do not guarantee inference availability or context-tier support.
      </p>
      </div>
    </div>
  );
}
