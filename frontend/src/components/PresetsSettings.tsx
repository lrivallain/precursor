import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, CheckCircle2, CircleHelp, GripVertical, LoaderCircle, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { modelsStore, useModelsVersion } from "../lib/modelsStore";
import { useSettings } from "../lib/settingsStore";
import type { AgentModelInfo, LLMProviderSpec, ModelCategories, ModelCategoryDef, ModelPreset, Settings } from "../lib/types";
import { AGENTS_PROVIDER, QUICK_START_CATEGORIES, slugifyCategory } from "../lib/modelPresetMenus";
import { checkCategories, providerSetupIssue, summarizePresets, type CatalogCheck, type PresetHealth, type PresetSummary } from "../lib/modelPresetHealth";
import { Select } from "./Select";
import { ProviderConfigurationBadge } from "./ProviderConfigurationBadge";

const MAX_CATEGORIES = 24;
const MAX_PRESETS = 12;
const EFFORT_LABELS: Record<string, string> = { "": "Auto", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max" };
const CONTEXT_STEPS = [16_000, 32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000];
const DRAG_TYPE = "application/x-precursor-preset";
// Provider identity is colour-coded so a mixed list reads at a glance.
const DOTS = ["bg-sky-500", "bg-violet-500", "bg-emerald-500", "bg-amber-500", "bg-rose-500", "bg-teal-500", "bg-indigo-500", "bg-orange-500"];
const CHIPS = [
  "bg-sky-500/10 text-sky-800 dark:text-sky-300", "bg-violet-500/10 text-violet-800 dark:text-violet-300",
  "bg-emerald-500/10 text-emerald-800 dark:text-emerald-300", "bg-amber-500/10 text-amber-800 dark:text-amber-300",
  "bg-rose-500/10 text-rose-800 dark:text-rose-300", "bg-teal-500/10 text-teal-800 dark:text-teal-300",
  "bg-indigo-500/10 text-indigo-800 dark:text-indigo-300", "bg-orange-500/10 text-orange-800 dark:text-orange-300",
];

type Payload = { kind: "model"; provider: string; model: string } | { kind: "entry"; category: string; index: number };
interface PaletteModel { provider: string; id: string; name: string; context_window?: number | null; efforts: string[]; publisher?: string }
interface DropTarget { category: string; index: number }

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M`;
  return n >= 1_000 ? `${Math.round(n / 1_000)}K` : String(n);
}

function signature(preset: ModelPreset): string {
  return JSON.stringify([preset.provider, preset.model, preset.reasoning_effort, preset.provider === AGENTS_PROVIDER ? preset.context_tier : preset.context_tokens]);
}

/** A new slot that never collides with an existing one: the engine matches a pinned model back to its category by this signature. */
function newPreset(model: PaletteModel, taken: Set<string>): ModelPreset | null {
  const agents = model.provider === AGENTS_PROVIDER;
  const window = model.context_window && model.context_window > 0 ? model.context_window : 128_000;
  const base = Math.min(window, 128_000);
  const contexts = agents ? [0] : [base, ...CONTEXT_STEPS.filter((step) => step <= window && step !== base).reverse()];
  const efforts = ["", ...model.efforts];
  for (const reasoning_effort of efforts) {
    for (const context of contexts) {
      const preset: ModelPreset = {
        provider: model.provider, model: model.id, reasoning_effort: reasoning_effort as ModelPreset["reasoning_effort"],
        context_tokens: agents ? 128_000 : context, context_tier: "default",
      };
      if (!taken.has(signature(preset))) return preset;
    }
  }
  if (agents) {
    const preset: ModelPreset = { provider: model.provider, model: model.id, reasoning_effort: "", context_tokens: 128_000, context_tier: "long_context" };
    if (!taken.has(signature(preset))) return preset;
  }
  return null;
}

function HealthIcon({ health }: { health: PresetHealth }) {
  if (health.issues.length) {
    return <span data-tooltip={health.issues.join("\n")} aria-label={health.issues.join(" ")} className="text-amber-700 dark:text-amber-400"><AlertTriangle size={14} aria-hidden="true" /></span>;
  }
  if (health.listed) return <span data-tooltip="Listed in the provider catalogue" aria-label="Listed in the provider catalogue" className="text-emerald-700 dark:text-emerald-400"><CheckCircle2 size={14} aria-hidden="true" /></span>;
  return <span data-tooltip="Not verified against a catalogue" aria-label="Not verified against a catalogue" className="text-muted"><CircleHelp size={14} aria-hidden="true" /></span>;
}

function SummaryBadge({ summary }: { summary: PresetSummary }) {
  const warn = summary.state === "review" || summary.state === "unverified";
  const Icon = summary.state === "valid" ? CheckCircle2 : warn ? AlertTriangle : summary.state === "checking" ? LoaderCircle : CircleHelp;
  const color = summary.state === "valid" ? "text-emerald-700 dark:text-emerald-400" : warn ? "text-amber-700 dark:text-amber-400" : "text-muted";
  return (
    <span data-tooltip={summary.detail} className={`inline-flex items-center gap-1 text-[11px] ${color}`}>
      <Icon size={12} aria-hidden="true" className={summary.state === "checking" ? "animate-spin" : ""} />
      {summary.label}
    </span>
  );
}

export function PresetsSettings({ value, onChange, settings, providers: allProviders }: {
  value: ModelCategories;
  onChange: (value: ModelCategories) => void;
  settings: Settings | null;
  providers: LLMProviderSpec[];
}) {
  useModelsVersion();
  const live = useSettings() ?? settings;
  const categories = value.categories;
  const [agentModels, setAgentModels] = useState<AgentModelInfo[]>([]);
  const [agentState, setAgentState] = useState<CatalogCheck["state"]>("unchecked");
  const [agentError, setAgentError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [drop, setDrop] = useState<DropTarget | null>(null);
  const [dragging, setDragging] = useState(false);
  const providers = useMemo(() => allProviders.filter((entry) => entry.id !== "mock"), [allProviders]);
  const payload = useRef<Payload | null>(null);
  const focusId = useRef<string | null>(null);

  const agentsReady = Boolean(live?.agents_enabled && live.agents_available && live.agents_runtime_started);
  const configured = useMemo(() => providers.filter((entry) => !providerSetupIssue(live, entry)), [providers, live]);
  const missing = useMemo(() => providers.filter((entry) => providerSetupIssue(live, entry)), [providers, live]);
  const providerOrder = useMemo(() => [...providers.map((entry) => entry.id), AGENTS_PROVIDER], [providers]);
  const label = useCallback((id: string) => id === AGENTS_PROVIDER ? "Agents (Copilot SDK)" : providers.find((entry) => entry.id === id)?.label ?? id, [providers]);
  const dot = (id: string) => DOTS[Math.max(0, providerOrder.indexOf(id)) % DOTS.length];
  const chip = (id: string) => CHIPS[Math.max(0, providerOrder.indexOf(id)) % CHIPS.length];

  const loadAgentModels = useCallback(async () => {
    if (!agentsReady) { setAgentState("setup"); setAgentModels([]); return; }
    setAgentState("checking");
    setAgentError(null);
    try {
      const list = await api.agents.listModels();
      setAgentModels(list);
      setAgentState(list.length ? "ready" : "unavailable");
      if (!list.length) setAgentError("The SDK returned no model catalogue.");
    } catch (error: unknown) {
      setAgentState("unavailable");
      setAgentError(error instanceof Error ? error.message : String(error));
    }
  }, [agentsReady]);

  useEffect(() => { void loadAgentModels(); }, [loadAgentModels]);
  useEffect(() => { for (const entry of configured) void modelsStore.ensureLoaded(entry.id); }, [configured]);
  useEffect(() => {
    if (!categories.length) setActiveId(null);
    else if (!activeId || !categories.some((entry) => entry.id === activeId)) setActiveId(categories[0].id);
  }, [categories, activeId]);
  useEffect(() => {
    if (!focusId.current) return;
    const input = document.querySelector<HTMLInputElement>(`[data-category-title="${focusId.current}"]`);
    focusId.current = null;
    input?.focus();
    input?.select();
  }, [categories.length]);

  const catalogs: Record<string, CatalogCheck> = {};
  for (const entry of providers) {
    const issue = providerSetupIssue(live, entry);
    const models = modelsStore.all(entry.id).filter((model) => model.id !== "auto");
    const error = modelsStore.error(entry.id);
    catalogs[entry.id] = issue ? { state: "setup", detail: issue, models: [] }
      : error ? { state: "unavailable", detail: `Catalogue unavailable: ${error}`, models: [] }
      : !modelsStore.hasLoaded(entry.id) ? { state: "checking", detail: "Reading the model catalogue.", models: [] }
      : !entry.discovers_models ? { state: "unsupported", detail: `${entry.label} does not publish a catalogue. Deployment ids need manual review.`, models }
      : { state: models.length ? "ready" : "unavailable", detail: models.length ? "Checked against the catalogue." : "No catalogue was returned.", models };
  }
  catalogs[AGENTS_PROVIDER] = !agentsReady
    ? { state: "setup", detail: "Enable and start the Copilot SDK runtime in Settings > Agents to use agent models.", models: [] }
    : { state: agentState, detail: agentError ?? "Checked against the Copilot SDK catalogue.", models: agentModels };
  const health = checkCategories(categories, catalogs);

  const palette: PaletteModel[] = [
    ...configured.flatMap((entry) => modelsStore.all(entry.id).filter((model) => model.id !== "auto").map((model) => ({
      provider: entry.id, id: model.id, name: model.name, context_window: model.context_window,
      efforts: model.supported_reasoning_efforts ?? [], publisher: model.publisher,
    }))),
    ...(agentsReady ? agentModels.filter((model) => model.id !== "auto").map((model) => ({
      provider: AGENTS_PROVIDER, id: model.id, name: model.name, efforts: model.supported_reasoning_efforts ?? [],
    })) : []),
  ];
  const needle = query.trim().toLowerCase();
  const visible = palette.filter((model) => (!filter || model.provider === filter)
    && (!needle || `${model.name} ${model.id} ${model.publisher ?? ""}`.toLowerCase().includes(needle)));
  const usage = new Map<string, string[]>();
  for (const category of categories) {
    for (const preset of category.presets) {
      const key = `${preset.provider}\u0000${preset.model}`;
      usage.set(key, [...(usage.get(key) ?? []), category.label]);
    }
  }
  const takenSignatures = new Set(categories.flatMap((category) => category.presets.map(signature)));
  const filterOptions = [...configured.map((entry) => entry.id), ...(agentsReady ? [AGENTS_PROVIDER] : [])];

  function commit(next: ModelCategoryDef[]): void {
    setNotice(null);
    onChange({ categories: next });
  }

  function patchCategory(id: string, patch: Partial<ModelCategoryDef>): void {
    commit(categories.map((entry) => entry.id === id ? { ...entry, ...patch } : entry));
  }

  function addCategory(title: string, id?: string): void {
    if (categories.length >= MAX_CATEGORIES) { setNotice(`You can have at most ${MAX_CATEGORIES} categories.`); return; }
    const taken = new Set(categories.map((entry) => entry.id));
    const slug = id && !taken.has(id) ? id : slugifyCategory(title, taken);
    commit([...categories, { id: slug, label: title, presets: [] }]);
    setActiveId(slug);
    focusId.current = slug;
  }

  function moveCategory(index: number, delta: number): void {
    const target = index + delta;
    if (target < 0 || target >= categories.length) return;
    const next = [...categories];
    [next[index], next[target]] = [next[target], next[index]];
    commit(next);
  }

  function removeCategory(id: string): void {
    if (confirmDelete !== id) { setConfirmDelete(id); return; }
    setConfirmDelete(null);
    commit(categories.filter((entry) => entry.id !== id));
  }

  function insert(categoryId: string, model: PaletteModel, index?: number): void {
    const category = categories.find((entry) => entry.id === categoryId);
    if (!category) return;
    if (category.presets.length >= MAX_PRESETS) { setNotice(`"${category.label}" is full (${MAX_PRESETS} models).`); return; }
    const preset = newPreset(model, takenSignatures);
    if (!preset) { setNotice("Every effort and context variant of this model is already in a category."); return; }
    const presets = [...category.presets];
    presets.splice(index ?? presets.length, 0, preset);
    patchCategory(categoryId, { presets });
    setActiveId(categoryId);
  }

  function moveEntry(from: DropTarget, to: DropTarget): void {
    const source = categories.find((entry) => entry.id === from.category);
    const target = categories.find((entry) => entry.id === to.category);
    const moved = source?.presets[from.index];
    if (!source || !target || !moved) return;
    if (source.id !== target.id && target.presets.length >= MAX_PRESETS) { setNotice(`"${target.label}" is full (${MAX_PRESETS} models).`); return; }
    const next = categories.map((entry) => ({ ...entry, presets: [...entry.presets] }));
    next.find((entry) => entry.id === source.id)!.presets.splice(from.index, 1);
    const destination = next.find((entry) => entry.id === target.id)!;
    const index = source.id === target.id && from.index < to.index ? to.index - 1 : to.index;
    destination.presets.splice(Math.max(0, Math.min(index, destination.presets.length)), 0, moved);
    commit(next);
  }

  function patchPreset(categoryId: string, index: number, patch: Partial<ModelPreset>): void {
    const category = categories.find((entry) => entry.id === categoryId);
    if (!category) return;
    patchCategory(categoryId, { presets: category.presets.map((preset, i) => i === index ? { ...preset, ...patch } : preset) });
  }

  function startDrag(event: DragEvent, next: Payload): void {
    payload.current = next;
    setDragging(true);
    event.dataTransfer.effectAllowed = "copyMove";
    event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(next));
    event.dataTransfer.setData("text/plain", next.kind === "model" ? next.model : "preset");
  }

  function endDrag(): void {
    payload.current = null;
    setDragging(false);
    setDrop(null);
  }

  function overList(event: DragEvent<HTMLElement>, category: ModelCategoryDef): void {
    if (!payload.current) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = payload.current.kind === "model" ? "copy" : "move";
    const rows = [...event.currentTarget.querySelectorAll<HTMLElement>("[data-preset-row]")];
    let index = rows.length;
    for (let i = 0; i < rows.length; i++) {
      const box = rows[i].getBoundingClientRect();
      if (event.clientY < box.top + box.height / 2) { index = i; break; }
    }
    setDrop((current) => current?.category === category.id && current.index === index ? current : { category: category.id, index });
  }

  function dropOn(event: DragEvent<HTMLElement>, category: ModelCategoryDef): void {
    event.preventDefault();
    const item = payload.current;
    const at = drop?.category === category.id ? drop.index : category.presets.length;
    endDrag();
    if (!item) return;
    if (item.kind === "entry") moveEntry({ category: item.category, index: item.index }, { category: category.id, index: at });
    else {
      const model = palette.find((entry) => entry.provider === item.provider && entry.id === item.model);
      if (model) insert(category.id, model, at);
    }
  }

  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  async function refresh(): Promise<void> {
    setRefreshing(true);
    try {
      await Promise.all([...configured.map((entry) => modelsStore.refresh(entry.id)), loadAgentModels()]);
      setRefreshedAt(Date.now());
    } finally {
      setRefreshing(false);
    }
  }

  function effortOptions(preset: ModelPreset) {
    const model = catalogs[preset.provider]?.models.find((entry) => entry.id === preset.model);
    const efforts = new Set<string>(["", ...(model?.supported_reasoning_efforts ?? [])]);
    if (preset.reasoning_effort) efforts.add(preset.reasoning_effort);
    return [...efforts].map((effort) => ({ value: effort, label: EFFORT_LABELS[effort] ?? effort }));
  }

  function contextOptions(preset: ModelPreset) {
    const model = catalogs[preset.provider]?.models.find((entry) => entry.id === preset.model);
    const window = model?.context_window ?? 0;
    const steps = new Set<number>(CONTEXT_STEPS.filter((step) => !window || step < window));
    if (window) steps.add(window);
    steps.add(preset.context_tokens);
    return [...steps].sort((a, b) => a - b).map((step) => ({ value: String(step), label: formatTokens(step) }));
  }

  const hasCategories = categories.length > 0;
  const totalModels = categories.reduce((sum, entry) => sum + entry.presets.length, 0);

  return (
    <div className="space-y-4" data-presets-settings>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-xs text-muted max-w-2xl">
          A preset is a named, ordered list of models. Pick it in a chat and Precursor tries the first model, then
          falls through to the next if it is unavailable. Mix providers freely: each slot keeps its own provider,
          effort and context size.
          <span className="block mt-1">
            Chats and topics can use any provider's models. Agents and workflows run on the Copilot SDK, so
            they only use entries tagged <span className="font-medium">Agents (Copilot SDK)</span>; add those
            to the same category to cover both.
          </span>
        </p>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={refreshing}
          aria-busy={refreshing}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-60"
        >
          <RefreshCw size={12} aria-hidden="true" className={refreshing ? "animate-spin" : ""} />
          {refreshing ? "Refreshing\u2026" : refreshedAt ? "Refreshed \u2713" : "Refresh catalogues"}
        </button>
      </div>

      {notice && <div role="alert" className="text-xs text-amber-800 dark:text-amber-300 bg-amber-500/10 rounded px-3 py-2">{notice}</div>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem] items-start">
        <section aria-label="Preset categories" className="space-y-3 min-w-0">
          {!hasCategories && (
            <p className="rounded-lg border border-dashed border-border p-4 text-xs text-muted">
              No presets yet. Create a category below, then add models to it.
            </p>
          )}

          {categories.map((category, categoryIndex) => {
            const checks = health[category.id] ?? [];
            const active = category.id === activeId;
            const targeted = drop?.category === category.id;
            return (
              <article
                key={category.id}
                data-category-card={category.id}
                aria-label={`${category.label} preset`}
                onClick={() => setActiveId(category.id)}
                className={`rounded-lg border bg-surface transition-colors ${targeted ? "border-accent ring-2 ring-accent/30" : active ? "border-accent/60" : "border-border"}`}
              >
                <header className="flex flex-wrap items-center gap-2 px-3 pt-3">
                  <input
                    data-category-title={category.id}
                    value={category.label}
                    maxLength={40}
                    aria-label="Category name"
                    onChange={(event) => patchCategory(category.id, { label: event.target.value })}
                    onBlur={() => { if (!category.label.trim()) patchCategory(category.id, { label: "Untitled" }); }}
                    className="min-w-0 flex-1 bg-transparent text-sm font-medium rounded px-1.5 py-1 -ml-1.5 border border-transparent hover:border-border focus:border-accent outline-none"
                  />
                  <SummaryBadge summary={summarizePresets(checks)} />
                  <div className="flex items-center">
                    <button type="button" aria-label={`Move ${category.label} up`} data-tooltip="Move up" disabled={categoryIndex === 0}
                      onClick={(event) => { event.stopPropagation(); moveCategory(categoryIndex, -1); }}
                      className="p-1.5 rounded text-muted hover:text-text hover:bg-bg disabled:opacity-30"><ArrowUp size={13} aria-hidden="true" /></button>
                    <button type="button" aria-label={`Move ${category.label} down`} data-tooltip="Move down" disabled={categoryIndex === categories.length - 1}
                      onClick={(event) => { event.stopPropagation(); moveCategory(categoryIndex, 1); }}
                      className="p-1.5 rounded text-muted hover:text-text hover:bg-bg disabled:opacity-30"><ArrowDown size={13} aria-hidden="true" /></button>
                    <button type="button" aria-label={confirmDelete === category.id ? `Confirm deleting ${category.label}` : `Delete ${category.label}`}
                      data-tooltip={confirmDelete === category.id ? "Click again to delete" : "Delete category"}
                      onClick={(event) => { event.stopPropagation(); removeCategory(category.id); }}
                      onBlur={() => setConfirmDelete((current) => current === category.id ? null : current)}
                      className={`p-1.5 rounded hover:bg-bg ${confirmDelete === category.id ? "text-red-600 dark:text-red-400" : "text-muted hover:text-text"}`}><Trash2 size={13} aria-hidden="true" /></button>
                  </div>
                </header>

                <div
                  className="px-3 pb-3 pt-2"
                  onDragOver={(event) => overList(event, category)}
                  onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDrop(null); }}
                  onDrop={(event) => dropOn(event, category)}
                >
                  {category.presets.length === 0 ? (
                    <div className={`rounded-md border border-dashed px-3 py-5 text-center text-xs ${dragging ? "border-accent text-accent bg-accent/5" : "border-border text-muted"}`}>
                      {dragging ? "Drop to add" : "Drag models here, or select this card and press + on a model."}
                    </div>
                  ) : (
                    <ol className="space-y-1.5">
                      {category.presets.map((preset, index) => {
                        const rowHealth = checks[index];
                        const agents = preset.provider === AGENTS_PROVIDER;
                        const model = catalogs[preset.provider]?.models.find((entry) => entry.id === preset.model);
                        return (
                          <li key={`${signature(preset)}-${index}`} data-preset-row>
                            {targeted && drop.index === index && <div className="h-0.5 -mt-1 mb-1 rounded bg-accent" aria-hidden="true" />}
                            <div
                              draggable
                              onDragStart={(event) => startDrag(event, { kind: "entry", category: category.id, index })}
                              onDragEnd={endDrag}
                              className="rounded-md border border-border bg-bg px-2 py-1.5 space-y-1.5"
                            >
                              <div className="flex items-center gap-2">
                                <span className="cursor-grab text-muted" aria-hidden="true"><GripVertical size={14} /></span>
                                <span className="w-4 text-center text-[11px] text-muted tabular-nums" data-tooltip={index === 0 ? "Tried first" : `Fallback ${index}`}>{index + 1}</span>
                                <span className="min-w-0 flex-1 truncate text-xs font-medium" data-tooltip={preset.model}>{model?.name ?? preset.model}</span>
                                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium ${chip(preset.provider)}`}>{label(preset.provider)}</span>
                                <div className="flex items-center shrink-0">
                                  <button type="button" aria-label={`Move ${preset.model} earlier`} data-tooltip="Try earlier" disabled={index === 0}
                                    onClick={() => moveEntry({ category: category.id, index }, { category: category.id, index: index - 1 })}
                                    className="p-1 rounded text-muted hover:text-text disabled:opacity-30"><ArrowUp size={12} aria-hidden="true" /></button>
                                  <button type="button" aria-label={`Move ${preset.model} later`} data-tooltip="Try later" disabled={index === category.presets.length - 1}
                                    onClick={() => moveEntry({ category: category.id, index }, { category: category.id, index: index + 2 })}
                                    className="p-1 rounded text-muted hover:text-text disabled:opacity-30"><ArrowDown size={12} aria-hidden="true" /></button>
                                  <button type="button" aria-label={`Remove ${preset.model}`} data-tooltip="Remove"
                                    onClick={() => patchCategory(category.id, { presets: category.presets.filter((_, i) => i !== index) })}
                                    className="p-1 rounded text-muted hover:text-red-600 dark:hover:text-red-400"><X size={12} aria-hidden="true" /></button>
                                </div>
                              </div>
                              <div className="flex flex-wrap items-center gap-2 pl-[1.9rem]">
                                <Select size="sm" ariaLabel={`Reasoning effort for ${preset.model}`} value={preset.reasoning_effort}
                                  onChange={(next) => patchPreset(category.id, index, { reasoning_effort: next as ModelPreset["reasoning_effort"] })}
                                  options={effortOptions(preset)} align="left" />
                                {agents ? (
                                  <Select size="sm" ariaLabel={`Context tier for ${preset.model}`} value={preset.context_tier}
                                    onChange={(next) => patchPreset(category.id, index, { context_tier: next as ModelPreset["context_tier"] })}
                                    options={[{ value: "default", label: "Default ctx" }, { value: "long_context", label: "Long ctx" }]} align="left" />
                                ) : (
                                  <Select size="sm" ariaLabel={`Context size for ${preset.model}`} value={String(preset.context_tokens)}
                                    onChange={(next) => patchPreset(category.id, index, { context_tokens: Number(next) })}
                                    options={contextOptions(preset)} align="left" />
                                )}
                                {rowHealth && <HealthIcon health={rowHealth} />}
                              </div>
                            </div>
                          </li>
                        );
                      })}
                      {targeted && drop.index === category.presets.length && <li aria-hidden="true"><div className="h-0.5 rounded bg-accent" /></li>}
                    </ol>
                  )}
                </div>
              </article>
            );
          })}

          <button
            type="button"
            onClick={() => addCategory("New category")}
            className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-border px-3 py-2.5 text-xs text-muted hover:border-accent hover:text-text"
          >
            <Plus size={13} aria-hidden="true" /> New category
          </button>
          {QUICK_START_CATEGORIES.some((entry) => !categories.some((c) => c.id === entry.id || c.label === entry.label)) && (
            <div className="flex flex-wrap items-center gap-1.5" data-ready-presets>
              <span className="text-[11px] text-muted">Suggestions:</span>
              {QUICK_START_CATEGORIES.filter((entry) => !categories.some((c) => c.id === entry.id || c.label === entry.label)).map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  data-tooltip={entry.hint}
                  onClick={() => addCategory(entry.label, entry.id)}
                  className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[11px] hover:border-accent hover:bg-accent/5"
                >
                  <Plus size={11} aria-hidden="true" /> {entry.label}
                </button>
              ))}
            </div>
          )}
          {hasCategories && <p className="text-[11px] text-muted">{categories.length} {categories.length === 1 ? "category" : "categories"} · {totalModels} {totalModels === 1 ? "model" : "models"}. Remember to Save.</p>}
        </section>

        <aside aria-label="Model palette" className="lg:max-h-[34rem] overflow-y-auto rounded-lg border border-border bg-surface p-3 space-y-3 lg:sticky lg:top-0 min-w-0">
          <div>
            <h3 className="text-sm font-medium">Models</h3>
            <p className="text-[11px] text-muted mt-0.5">
              {hasCategories ? <>Drag onto a category, or press <span className="font-medium">+</span> to add to <span className="font-medium">{categories.find((entry) => entry.id === activeId)?.label}</span>.</> : "Create a category to start adding models."}
            </p>
          </div>
          <div className="relative">
            <Search size={13} aria-hidden="true" className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search models"
              aria-label="Search models"
              className="w-full bg-bg border border-border rounded pl-7 pr-2 py-1.5 text-xs outline-none focus:border-accent"
            />
          </div>
          {filterOptions.length > 1 && (
            <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by provider">
              {["", ...filterOptions].map((id) => (
                <button
                  key={id || "all"}
                  type="button"
                  aria-pressed={filter === id}
                  onClick={() => setFilter(id)}
                  className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${filter === id ? "border-accent bg-accent/10 text-text" : "border-border text-muted hover:text-text"}`}
                >
                  {id && <span className={`h-1.5 w-1.5 rounded-full ${dot(id)}`} aria-hidden="true" />}
                  {id ? label(id) : "All"}
                </button>
              ))}
            </div>
          )}
          <ul className="max-h-[28rem] overflow-y-auto space-y-1 -mr-1 pr-1">
            {visible.map((model) => {
              const used = usage.get(`${model.provider}\u0000${model.id}`);
              return (
                <li
                  key={`${model.provider}:${model.id}`}
                  draggable={hasCategories}
                  onDragStart={(event) => startDrag(event, { kind: "model", provider: model.provider, model: model.id })}
                  onDragEnd={endDrag}
                  data-palette-model={`${model.provider}:${model.id}`}
                  className={`flex items-center gap-2 rounded-md border border-border bg-bg px-2 py-1.5 ${hasCategories ? "cursor-grab" : ""}`}
                >
                  <span className={`h-2 w-2 shrink-0 rounded-full ${dot(model.provider)}`} aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium" data-tooltip={model.id}>{model.name}</span>
                    <span className="block truncate text-[11px] text-muted">{label(model.provider)}{used ? ` · in ${used.join(", ")}` : ""}</span>
                  </span>
                  <button
                    type="button"
                    aria-label={`Add ${model.name} to ${categories.find((entry) => entry.id === activeId)?.label ?? "a category"}`}
                    data-tooltip={hasCategories ? "Add to the selected category" : "Create a category first"}
                    disabled={!activeId}
                    onClick={() => { const model_ = model; if (activeId) insert(activeId, model_); }}
                    className="p-1 rounded text-muted hover:text-accent disabled:opacity-30"
                  >
                    <Plus size={14} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
            {!visible.length && (
              <li className="text-[11px] text-muted px-1 py-3 text-center">
                {palette.length ? "No model matches." : configured.length || agentsReady ? "Loading models..." : "No provider is configured yet."}
              </li>
            )}
          </ul>
          {missing.length > 0 && (
            <div className="border-t border-border pt-2 space-y-1">
              <div className="text-[11px] text-muted">Configure these in Settings &gt; Model to use their models:</div>
              <ul className="flex flex-wrap gap-1.5">
                {missing.map((entry) => (
                  <li key={entry.id} className="inline-flex items-center gap-1 text-[11px]">{entry.label} <ProviderConfigurationBadge settings={live} provider={entry} /></li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
