import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { api } from "../lib/api";
import { modelsStore, useCurrentModel, useModelsVersion } from "../lib/modelsStore";
import { settingsStore, useSettings } from "../lib/settingsStore";
import type { AgentModelInfo, LLMModel } from "../lib/types";

interface MenuOption {
  value: string;
  label: string;
}

interface MenuGroup {
  label?: string;
  options: MenuOption[];
}

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

  const grouped = models.reduce<Record<string, LLMModel[]>>((acc, m) => {
    const k = m.publisher || "Other";
    (acc[k] ||= []).push(m);
    return acc;
  }, {});
  const modelGroups: MenuGroup[] = Object.entries(grouped).map(([label, list]) => ({
    label,
    options: list.map((m) => ({ value: m.id, label: m.name })),
  }));
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
      <SelectMenu
        ariaLabel="Model"
        tooltip="Model (applies to every conversation)"
        triggerLabel={modelLabel || "Select a model\u2026"}
        value={inCatalog ? modelId : ""}
        groups={modelGroups}
        menuMinWidthClass="min-w-[18rem]"
        emptyHint="No model catalog — set one in Settings."
        disabled={saving}
        onSelect={onModelChange}
      />
      {supportedEfforts.length > 0 && (
        <SelectMenu
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
        <SelectMenu
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
      <SelectMenu
        ariaLabel="Agent model"
        tooltip="Default model for new agent sessions"
        triggerLabel={label}
        value={defaultModel}
        groups={[{ options }]}
        menuMinWidthClass="min-w-[18rem]"
        disabled={saving}
        onSelect={onModelChange}
      />
      {supportedEfforts.length > 0 && (
        <SelectMenu
          ariaLabel="Reasoning effort"
          tooltip="Reasoning effort (supported by this model)"
          triggerLabel={effortLabelText}
          value={effort}
          groups={[{ options: effortOptions }]}
          disabled={saving}
          onSelect={(v) => void save({ agents_reasoning_effort: v })}
        />
      )}
      <SelectMenu
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

function SelectMenu({
  ariaLabel,
  tooltip,
  triggerLabel,
  value,
  groups,
  emptyHint,
  menuMinWidthClass = "min-w-[11rem]",
  disabled,
  onSelect,
}: {
  ariaLabel: string;
  tooltip: string;
  triggerLabel: string;
  value: string;
  groups: MenuGroup[];
  emptyHint?: string;
  menuMinWidthClass?: string;
  disabled: boolean;
  onSelect: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointerDown(e: PointerEvent): void {
      if (!rootRef.current?.contains(e.target as Node | null)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onDocPointerDown);
    document.addEventListener("keydown", onKeyDown);
    // Bring the active row into view when the menu opens.
    selectedRef.current?.scrollIntoView({ block: "nearest" });
    return () => {
      document.removeEventListener("pointerdown", onDocPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const hasOptions = groups.some((g) => g.options.length > 0);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-tooltip={tooltip}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted hover:text-text hover:bg-bg outline-none disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <span className="max-w-[13rem] truncate">{triggerLabel}</span>
        <ChevronDown size={13} className="shrink-0 opacity-70" />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label={ariaLabel}
          className={`absolute bottom-full left-0 z-30 mb-2 max-h-72 overflow-y-auto rounded-xl border border-border bg-surface p-1 shadow-xl ${menuMinWidthClass}`}
        >
          {!hasOptions && (
            <div className="px-2 py-1.5 text-xs text-muted">{emptyHint ?? "No options"}</div>
          )}
          {groups.map((group, gi) => (
            <div key={group.label ?? gi}>
              {group.label && group.options.length > 0 && (
                <div className="px-2 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                  {group.label}
                </div>
              )}
              {group.options.map((opt) => {
                const selected = opt.value === value;
                return (
                  <button
                    key={opt.value}
                    ref={selected ? selectedRef : undefined}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => {
                      onSelect(opt.value);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-border/50 ${
                      selected ? "text-text" : "text-text/90"
                    }`}
                  >
                    <span className="flex w-4 shrink-0 justify-center">
                      {selected && <Check size={14} className="text-accent" />}
                    </span>
                    <span className="truncate">{opt.label}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
