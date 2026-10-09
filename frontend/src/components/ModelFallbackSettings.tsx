import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArrowDown, ArrowUp, CheckCircle2, CircleHelp, LoaderCircle, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { AgentModelInfo, LLMModel, LLMProviderSpec, ModelCategories, ModelCategory, ModelPreset, Settings } from "../lib/types";
import { Modal } from "./Modal";
import { ComposerSelectMenu, type MenuGroup } from "./ComposerSelectMenu";
import { groupModelsByPublisher } from "./ComposerModelControls";
import { useSettings } from "../lib/settingsStore";
import { MODEL_PRESET_CATEGORIES as CATEGORIES } from "../lib/modelPresetMenus";
import { checkPresets, providerSetupIssue, summarizePresetConfigurations, summarizePresets, type CatalogCheck, type PresetSummary } from "../lib/modelPresetHealth";

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
  const [category, setCategory] = useState<ModelCategory>("efficiency");
  const [draft, setDraft] = useState<Record<string, ModelCategories> | null>(null);
  const editing = draft !== null;
  const summaryRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [agentModels, setAgentModels] = useState<AgentModelInfo[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [agentCatalogState, setAgentCatalogState] = useState<CatalogCheck["state"]>("unchecked");
  const [checking, setChecking] = useState(false);
  const agentRequest = useRef(0);
  const editorId = useId();
  const editorHeadingId = useId();
  const dialogHeadingId = useId();
  const dialogDescriptionId = useId();
  const agents = scope === "agents";
  const scopeId = agents ? "agents" : provider;
  const categories = (draft ?? value)[scopeId] ?? EMPTY;
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
  const editorChatHealth = checkPresets((draft ?? value)[provider] ?? EMPTY, chatCheck, false);
  const editorAgentHealth = checkPresets((draft ?? value).agents ?? EMPTY, agentCheck, true);
  const categoryChecks = agents ? editorAgentHealth : editorChatHealth;
  const categoryInfo = CATEGORIES.find((entry) => entry.id === category)!;
  const categoryHealth = summarizePresets(categoryChecks[category], activeCheck);
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

  useEffect(() => {
    if (!editing) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const parent = summaryRef.current?.closest<HTMLElement>('[aria-modal="true"]');
    const hidden = parent?.getAttribute("aria-hidden");
    const wasInert = parent?.hasAttribute("inert");
    parent?.setAttribute("inert", "");
    parent?.setAttribute("aria-hidden", "true");
    dialogRef.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus();
    function onEscape(event: globalThis.KeyboardEvent): void {
      if (event.key !== "Escape" || (event.target instanceof Element && event.target.closest('[role="listbox"]'))) return;
      // Settings owns a window-level Escape handler; the nested editor must
      // consume its own dismissal even before focus reaches a child control.
      event.preventDefault();
      event.stopPropagation();
      setDraft(null);
    }
    window.addEventListener("keydown", onEscape, true);
    return () => {
      window.removeEventListener("keydown", onEscape, true);
      if (!wasInert) parent?.removeAttribute("inert");
      if (hidden == null) parent?.removeAttribute("aria-hidden");
      else parent?.setAttribute("aria-hidden", hidden);
      previousFocus?.focus();
    };
  }, [editing]);

  async function refreshChecks(): Promise<void> {
    setChecking(true);
    try {
      await Promise.all([onCheckModels(), agentReady ? loadAgentModels() : Promise.resolve()]);
    } finally {
      setChecking(false);
    }
  }

  function replace(category: ModelCategory, presets: ModelPreset[]): void {
    setDraft((current) => current ? { ...current, [scopeId]: { ...(current[scopeId] ?? EMPTY), [category]: presets } } : null);
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
  const currentCategory = agents ? effectiveSettings?.agents_model_category : effectiveSettings?.llm_model_category;
  const providerLabel = providers.find((p) => p.id === provider)?.label ?? provider;
  const configurations = [
    { id: "provider" as const, label: "Chat & live", runtime: providerLabel, key: provider, check: chatCheck, health: chatHealth, editorHealth: editorChatHealth },
    { id: "agents" as const, label: "Agents & workflows", runtime: "Copilot SDK", key: "agents", check: agentCheck, health: agentHealth, editorHealth: editorAgentHealth },
  ];
  const overallHealth = summarizePresetConfigurations(configurations.map((configuration) => ({
    label: configuration.label, checks: Object.values(configuration.health).flat(), catalog: configuration.check,
  })));
  const needsReview = ["review", "setup", "unverified"].includes(overallHealth.state);

  function openEditor(review = false): void {
    setDraft(structuredClone(value));
    if (!review) return;
    for (const configuration of configurations) {
      const issue = CATEGORIES.find((entry) => configuration.health[entry.id].some((preset) => preset.issues.length));
      if (issue) {
        setScope(configuration.id);
        setCategory(issue.id);
        return;
      }
    }
    for (const configuration of configurations) {
      const issue = CATEGORIES.find((entry) => {
        const health = summarizePresets(configuration.health[entry.id], configuration.check);
        return ["setup", "unverified"].includes(health.state);
      });
      if (issue) {
        setScope(configuration.id);
        setCategory(issue.id);
        return;
      }
    }
  }

  function applyDraft(): void {
    if (draft) onChange(draft);
    setDraft(null);
  }

  function handleDialogKey(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.target instanceof Element && event.target.closest('[role="listbox"]')) {
      event.stopPropagation();
      return;
    }
    if (event.key === "Escape") {
      event.stopPropagation();
      event.preventDefault();
      setDraft(null);
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]),input:not([disabled]),select:not([disabled]),summary,[href],[tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.getClientRects().length > 0);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  return (
    <>
    <div ref={summaryRef} className="mt-6 space-y-3 break-words" data-model-fallback-settings>
      <h3 className="text-sm font-medium">Model alternatives</h3>
      <p className="text-[11px] text-muted">Optional, separate configurations for chat and agent tasks.</p>
      <div className="space-y-2" aria-label="Model alternative configuration summary">
        {configurations.map((configuration) => {
          const categories = value[configuration.key] ?? EMPTY;
          const count = presetCount(categories);
          const configured = Object.values(categories).filter((presets) => presets.length).length;
          return (
            <div key={configuration.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs" data-model-fallback-summary={configuration.id}>
              <span className="font-medium">{configuration.label}</span>
              <span className="text-muted">{count ? `${count} ${count === 1 ? "preset" : "presets"} in ${configured}/3 categories` : "Not configured"}</span>
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
        <div role="status" data-model-fallback-health={overallHealth.state}><HealthBadge {...overallHealth} /></div>
        <button type="button" onClick={() => void refreshChecks()} disabled={checking || modelsLoading || (agentReady && agentCatalogState === "checking") || !provider} className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-xs hover:bg-surface disabled:opacity-50">
          <RefreshCw size={12} aria-hidden="true" className={checking ? "animate-spin" : ""} />
          {checking ? "Checking models..." : "Check models"}
        </button>
      </div>
      <p className="text-[11px] text-muted">Catalogue checks only; no inference requests.</p>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => openEditor()} className="rounded bg-accent px-3 py-1.5 text-xs text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Manage presets</button>
        {needsReview && <button type="button" onClick={() => openEditor(true)} className="text-xs text-accent hover:underline">Review configuration</button>}
      </div>
    </div>
    {editing && createPortal(
      <Modal
        onClose={() => setDraft(null)}
        zIndex="z-[60]"
        padded
        labelledBy={dialogHeadingId}
        describedBy={dialogDescriptionId}
        panelClassName="w-full max-w-4xl overflow-hidden rounded-lg border border-border bg-bg shadow-2xl"
        closeOnBackdrop={false}
      >
        <div ref={dialogRef} onKeyDown={handleDialogKey} className="flex max-h-[calc(100dvh-2rem)] flex-col" data-model-fallback-dialog>
          <header className="shrink-0 border-b border-border px-4 py-3 sm:px-6">
            <div className="flex items-center justify-between gap-3">
              <h2 id={dialogHeadingId} className="text-sm font-semibold">Manage model alternatives</h2>
              <button type="button" onClick={() => setDraft(null)} aria-label="Close preset editor" data-tooltip="Cancel preset changes (Esc)" className="rounded p-1.5 hover:bg-surface"><X size={16} /></button>
            </div>
            <p id={dialogDescriptionId} className="mt-1 text-xs text-muted">Select a category and runtime. Only that list is expanded for editing.</p>
          </header>
          <div className="@container min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
            <div className="grid grid-cols-[minmax(5rem,.7fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2" role="group" aria-label="Categories and runtimes" data-model-fallback-overview>
              <span className="px-1 text-xs font-medium">Category</span>
              {configurations.map((configuration) => <div key={configuration.id} className="px-1 text-xs font-medium">{configuration.label}<span className="mt-0.5 block text-[11px] font-normal text-muted">{configuration.runtime}</span></div>)}
              {CATEGORIES.map((entry) => <div key={entry.id} className="contents">
                <div className="border-t border-border px-1 py-3"><span className="text-xs font-medium">{entry.label}</span><p className="mt-0.5 hidden text-[11px] text-muted @sm:block">{entry.hint}</p></div>
                {configurations.map((configuration) => {
                  const presets = (draft?.[configuration.key] ?? EMPTY)[entry.id];
                  const health = summarizePresets(configuration.editorHealth[entry.id], configuration.check);
                  const selected = scope === configuration.id && category === entry.id;
                  return (
                    <button
                      key={configuration.id}
                      type="button"
                      aria-label={`Edit ${configuration.label} ${entry.label}`}
                      aria-pressed={selected}
                      aria-controls={editorId}
                      data-model-fallback-cell={`${configuration.id}-${entry.id}`}
                      data-category-health={health.state}
                      onClick={() => { setScope(configuration.id); setCategory(entry.id); }}
                      className={`min-w-0 rounded border px-2 py-2.5 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:px-3 ${selected ? "border-accent bg-surface" : "border-border hover:bg-surface"}`}
                    >
                      <span className="mb-1 block text-xs font-medium">{presets.length ? `${presets.length} ${presets.length === 1 ? "preset" : "presets"}` : "+ Configure"}</span>
                      <HealthBadge {...health} />
                    </button>
                  );
                })}
              </div>)}
            </div>
            <section id={editorId} aria-labelledby={editorHeadingId} className="mt-5 border-t border-border pt-4" data-model-fallback-category={category} data-category-health={categoryHealth.state}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div><h3 id={editorHeadingId} className="text-sm font-medium">{categoryInfo.label} / {agents ? "Agents & workflows" : "Chat & live"}</h3><p className="mt-0.5 text-[11px] text-muted">{agents ? "Copilot SDK - context tier" : `${providerLabel} - context-token budget`}. Alternatives tried top to bottom.</p></div>
                <HealthBadge {...categoryHealth} />
              </div>
              {activeCheck.state !== "ready" && activeCheck.state !== "checking" && <p role="status" className="mt-3 text-xs text-amber-700 dark:text-amber-400">{activeCheck.detail} Presets are kept, but their models are not verified.</p>}
              {agents && currentModel === "auto" && <p className="mt-3 text-[11px] text-muted">The SDK selection is Auto. Choose an explicit agent model to categorise it; Auto keeps the runtime's own routing.</p>}
              {activeCheck.state === "ready" && categoryHealth.total > 0 && categoryHealth.listed === 0 && <p className="mt-3 text-[11px] text-amber-700 dark:text-amber-400">No listed alternatives remain in this category. Add a currently listed model.</p>}
              {categories[category].length === 0 && <p className="py-5 text-xs text-muted">No alternatives configured. This category is optional.</p>}
              {categories[category].length > 0 && <div className="mt-3 hidden grid-cols-[1.5rem_minmax(0,1fr)_7rem_9rem_5rem] gap-2 text-[11px] text-muted @lg:grid" aria-hidden="true"><span>#</span><span>Model</span><span>Effort</span><span>{agents ? "Context tier" : "Context budget"}</span><span>Order</span></div>}
              {categories[category].map((preset, index) => (
                <div key={`${scope}-${category}-${index}`} className="border-b border-border py-3" data-preset-health={categoryChecks[category][index].issues.length ? "review" : categoryChecks[category][index].listed ? "valid" : "unverified"}>
                  <div className="grid grid-cols-[1.5rem_minmax(0,1fr)_5rem] items-start gap-2 @lg:grid-cols-[1.5rem_minmax(0,1fr)_7rem_9rem_5rem]">
                    <span className="pt-1.5 text-[11px] text-muted">{index + 1}.</span>
                    <div className="min-w-0">
                      <ComposerSelectMenu
                        ariaLabel={`${categoryInfo.label} model ${index + 1}`}
                        tooltip={preset.model ? `Model: ${preset.model}` : "Select a model for this preset"}
                        triggerLabel={catalog.find((model) => model.id === preset.model)?.name ?? (preset.model || "Select a model...")}
                        value={preset.model}
                        groups={modelGroups}
                        disabled={false}
                        fullWidth
                        portal
                        filterPlaceholder="Filter models…"
                        emptyHint="No catalog - enter a model id in the search."
                        onSelect={(model) => update(category, index, { model })}
                        onCustomValue={(model) => update(category, index, { model })}
                      />
                    </div>
                    <div className="col-start-2 min-w-0 @lg:col-start-auto">
                      <span className="mb-1 block text-[11px] text-muted @lg:hidden">Effort</span>
                      <ComposerSelectMenu ariaLabel={`${categoryInfo.label} effort ${index + 1}`} tooltip="Effort for this preset" value={preset.reasoning_effort} triggerLabel={preset.reasoning_effort || "Auto"} groups={[{ options: EFFORTS.map((effort) => ({ value: effort, label: effort || "Auto (omit effort)" })) }]} disabled={false} fullWidth portal onSelect={(value) => {
                        const effort = EFFORTS.find((effort) => effort === value);
                        if (effort !== undefined) update(category, index, { reasoning_effort: effort });
                      }} />
                    </div>
                    <div className="col-start-2 min-w-0 @lg:col-start-auto">
                      <span className="mb-1 block text-[11px] text-muted @lg:hidden">{agents ? "Context tier" : "Context budget (tokens)"}</span>
                      {agents ? (
                        <ComposerSelectMenu ariaLabel={`${categoryInfo.label} context ${index + 1}`} tooltip="Context tier for this preset" value={preset.context_tier} triggerLabel={preset.context_tier === "long_context" ? "Long context" : "Default"} groups={[{ options: [{ value: "default", label: "Default" }, { value: "long_context", label: "Long context" }] }]} disabled={false} fullWidth portal onSelect={(tier) => {
                          if (tier === "default" || tier === "long_context") update(category, index, { context_tier: tier });
                        }} />
                      ) : (
                        <input className={INPUT_CLASS} type="number" min={1000} max={5000000} step={1000} aria-label={`${categoryInfo.label} context ${index + 1}`} value={preset.context_tokens} onChange={(event) => update(category, index, { context_tokens: Number(event.target.value) })} />
                      )}
                    </div>
                    <div className="col-start-3 row-start-1 flex @lg:col-start-auto @lg:row-start-auto">
                      <button type="button" aria-label={`Move ${categoryInfo.label} preset ${index + 1} up`} data-tooltip="Try earlier" disabled={index === 0} onClick={() => move(category, index, -1)} className="rounded p-1.5 hover:bg-surface disabled:opacity-30"><ArrowUp size={13} /></button>
                      <button type="button" aria-label={`Move ${categoryInfo.label} preset ${index + 1} down`} data-tooltip="Try later" disabled={index === categories[category].length - 1} onClick={() => move(category, index, 1)} className="rounded p-1.5 hover:bg-surface disabled:opacity-30"><ArrowDown size={13} /></button>
                      <button type="button" aria-label={`Remove ${categoryInfo.label} preset ${index + 1}`} data-tooltip="Remove preset" onClick={() => replace(category, categories[category].filter((_, entry) => entry !== index))} className="rounded p-1.5 hover:bg-surface"><Trash2 size={13} /></button>
                    </div>
                  </div>
                  <div className="mt-2 pl-8">
                    <HealthBadge
                      state={categoryChecks[category][index].issues.length ? "review" : categoryChecks[category][index].listed ? "valid" : "unverified"}
                      label={categoryChecks[category][index].issues.length ? "Review needed" : categoryChecks[category][index].listed ? "Model listed" : "Model not checked"}
                      detail={categoryChecks[category][index].issues.join("\n") || activeCheck.detail}
                    />
                    {categoryChecks[category][index].issues.length > 0 && (
                      <ul className="mt-1 space-y-1 text-[11px] text-amber-700 dark:text-amber-400">
                        {categoryChecks[category][index].issues.map((issue) => <li key={issue}>{issue}</li>)}
                      </ul>
                    )}
                  </div>
                </div>
              ))}
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" onClick={() => add(category)} disabled={categories[category].length >= 12} className="flex items-center gap-1 text-xs text-accent disabled:opacity-40"><Plus size={13} />Add preset</button>
                <button type="button" onClick={() => add(category, true)} disabled={Boolean(currentCategory) || !currentModel || currentModel === "auto" || categories[category].length >= 12} data-tooltip={currentCategory ? "Select a direct model to capture a fixed model/effort/context profile" : "Capture the current direct model selection"} className="text-xs text-accent disabled:opacity-40">Add current selection</button>
              </div>
              <details className="mt-4 border-t border-border pt-3 text-[11px] text-muted">
                <summary className="cursor-pointer">How alternatives and checks work</summary>
                <p className="mt-2">
                  Keep retired ids here so Precursor can still identify their category.
                  If a model is in multiple categories, its effort and context must match one
                  unambiguously. Alternatives never cross providers or change your saved selection.
                  Retries stop once output or agent actions begin.
                  {" "}Listed model ids do not guarantee inference availability or context-tier support.
                </p>
              </details>
            </section>
          </div>
          <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 sm:px-6">
            <span className="text-[11px] text-muted">Apply updates the Settings draft. Save Settings to persist.</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => setDraft(null)} className="rounded border border-border px-3 py-1.5 text-xs hover:bg-surface">Cancel</button>
              <button type="button" onClick={applyDraft} className="rounded bg-accent px-3 py-1.5 text-xs text-white">Apply to settings</button>
            </div>
          </footer>
        </div>
      </Modal>,
      document.body,
    )}
    </>
  );
}
