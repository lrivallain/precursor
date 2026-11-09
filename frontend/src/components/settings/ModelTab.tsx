import { ProviderConfigurationBadge } from "../ProviderConfigurationBadge";
import { Select } from "../Select";
import { OpenAIProxySettings } from "../OpenAIProxySettings";
import { ProviderSetup } from "./ProviderSetup";
import { NumberInput } from "./NumberInput";
import type { ModelSettings } from "./useModelSettings";
import type { SystemSettingsState } from "./useSystemSettings";

export function ModelTab({ m, system }: { m: ModelSettings; system: SystemSettingsState }) {
  const { settings, adopt, models, modelsError, provider, providers, liveSettings } = m;
  return (
    <section>
      <GlobalDefaults m={m} />
      <h3 className="text-sm font-medium mb-2">Configure provider</h3>
      <Select
        value={provider}
        onChange={m.onProviderChange}
        options={providers.map((p) => ({
          value: p.id,
          label: p.label,
          suffix: <ProviderConfigurationBadge settings={liveSettings} provider={p} />,
        }))}
        ariaLabel="LLM provider"
        fullWidth
      />
      <ProviderSetup m={m} />
      <CatalogStatus m={m} />
      <p className="text-[11px] text-muted mt-2">
        Configure several providers without changing the global default.
        Chat and topic model pickers list all configured providers and save
        the provider with your model choice, effort and context size.
        Use global default removes that override. Group models from any
        provider into ordered fallbacks in Settings &gt; Presets.
      </p>
      {system.sys && <PromptBudgeting system={system} />}
      <OpenAIProxySettings
        settings={settings}
        onSettings={adopt}
        models={provider === settings?.llm_provider && !modelsError ? models : null}
      />
    </section>
  );
}

function GlobalDefaults({ m }: { m: ModelSettings }) {
  const { settings, providers, defaultProvider, defaultModel, defaultModels, mockEnabled, liveSettings } = m;
  // Mock stays listed while it is the default, so the picker never goes blank.
  const offered = providers.filter((p) => p.id !== "mock" || mockEnabled || defaultProvider === "mock");
  const placeholder = settings?.llm_model_category ? "Preset category (set in the composer)" : "Select a model…";
  return (
    <>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium mb-2">Global default provider</h3>
          <Select
            value={defaultProvider}
            onChange={m.chooseDefaultProvider}
            options={offered.map((p) => ({ value: p.id, label: p.label, suffix: <ProviderConfigurationBadge settings={liveSettings} provider={p} /> }))}
            ariaLabel="Global default provider"
            fullWidth
          />
        </div>
        <div className="min-w-0">
          <h3 className="text-sm font-medium mb-2">Global default model</h3>
          <Select
            value={defaultModel}
            onChange={m.setDefaultModel}
            options={[
              ...(defaultModel === "" ? [{ value: "", label: placeholder }] : []),
              ...defaultModels.map((model) => ({ value: model.id, label: model.name || model.id })),
            ]}
            ariaLabel="Global default model"
            fullWidth
          />
        </div>
      </div>
      <p className="text-[11px] text-muted mt-2 mb-4">
        Applies to conversations using the global default. Agents stay on the Copilot SDK.
        {" "}Provider marks reflect saved setup, not a connection test.
      </p>
    </>
  );
}

function catalogLabel(models: number, error: string | null): string {
  if (error) return `Catalog unavailable: ${error}.`;
  return models > 0 ? `${models} models` : "No catalog for this provider.";
}

function CatalogStatus({ m }: { m: ModelSettings }) {
  return (
    <div className="flex items-center gap-2 mt-4">
      <button
        type="button"
        onClick={() => void m.applyProviderSettings()}
        disabled={m.modelsLoading}
        className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50"
      >
        {m.modelsLoading ? "Refreshing…" : "Apply & refresh models"}
      </button>
      <span className="text-[11px] text-muted">{catalogLabel(m.models.length, m.modelsError)}</span>
    </div>
  );
}

const BUDGET_FIELDS = [
  { key: "llm_max_input_tokens", label: "Max input tokens per request", min: 1000, max: 5_000_000 },
  {
    key: "llm_max_tool_result_tokens",
    label: "Max tokens per tool result",
    min: 100,
    max: 2_000_000,
    help: "Per-message ceiling applied to individual tool outputs before they enter the transcript.",
  },
  {
    key: "llm_tool_result_keep_turns",
    label: "Keep tool results in full for the last N turns",
    min: 0,
    max: 1000,
    help: "Larger tool outputs from older turns reach the model as a short preview; it can call the tool again when it needs the rest. 0 always sends them in full.",
  },
  {
    key: "llm_max_attachment_chars",
    label: "Max characters per attached document",
    min: 1000,
    max: 2_000_000,
    help: "Text extracted from each attached PDF, DOCX, PPTX or text file. Longer documents are cut, and the model is told how much it can't see.",
  },
] as const;

function PromptBudgeting({ system: { sys, setSys } }: { system: SystemSettingsState }) {
  if (!sys) return null;
  return (
    <div className="mt-6 space-y-3">
      <h3 className="text-sm font-medium">Prompt budgeting</h3>
      <p className="text-[11px] text-muted">
        Caps how much of the transcript is sent to the model so a
        few large reads / tool results can't overflow the context
        window. Lower these for smaller models.
      </p>
      {BUDGET_FIELDS.map((f) => (
        <div key={f.key}>
          <label className="block text-xs text-muted mb-1">{f.label}</label>
          <NumberInput
            value={sys[f.key]}
            min={f.min}
            max={f.max}
            onCommit={(n) => setSys({ ...sys, [f.key]: n })}
          />
          {"help" in f && <p className="text-[11px] text-muted mt-1">{f.help}</p>}
        </div>
      ))}
    </div>
  );
}
