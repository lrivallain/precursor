import { ProviderConfigurationBadge } from "../ProviderConfigurationBadge";
import { settingsStore } from "../../lib/settingsStore";
import { Select } from "../Select";
import { DeploymentNamesInput } from "../DeploymentNamesInput";
import { OpenAIProxySettings } from "../OpenAIProxySettings";
import { NumberInput } from "./NumberInput";
import type { SettingsPanelState } from "./useSettingsPanelState";

export function ModelTab({ s }: { s: SettingsPanelState }) {
  const {
    settings,
    setSettings,
    models,
    modelsError,
    modelsLoading,
    provider,
    defaultProvider,
    setDefaultProvider,
    defaultModel,
    setDefaultModel,
    defaultModels,
    providers,
    providerConfig,
    setProviderConfig,
    clearedSecrets,
    setClearedSecrets,
    mockEnabled,
    setMockEnabled,
    sys,
    setSys,
    onProviderChange,
    liveSettings,
    activeProviderSpec,
    applyProviderSettings,
  } = s;
  return (
    <section>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium mb-2">Global default provider</h3>
          <Select
            value={defaultProvider}
            onChange={(next) => { setDefaultProvider(next); setDefaultModel(""); }}
            options={providers.filter((p) => p.id !== "mock" || mockEnabled || defaultProvider === "mock").map((p) => ({ value: p.id, label: p.label, suffix: <ProviderConfigurationBadge settings={liveSettings} provider={p} /> }))}
            ariaLabel="Global default provider"
            fullWidth
          />
        </div>
        <div className="min-w-0">
          <h3 className="text-sm font-medium mb-2">Global default model</h3>
          <Select
            value={defaultModel}
            onChange={setDefaultModel}
            options={[
              ...(defaultModel === "" ? [{ value: "", label: settings?.llm_model_category ? "Preset category (set in the composer)" : "Select a model…" }] : []),
              ...defaultModels.map((m) => ({ value: m.id, label: m.name || m.id })),
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
      <h3 className="text-sm font-medium mb-2">Configure provider</h3>
      <Select
        value={provider}
        onChange={onProviderChange}
        options={providers.map((p) => ({
          value: p.id,
          label: p.label,
          suffix: <ProviderConfigurationBadge settings={liveSettings} provider={p} />,
        }))}
        ariaLabel="LLM provider"
        fullWidth
      />

      {(() => {
        const activeSpec = activeProviderSpec;
        if (!activeSpec) return null;
        if (activeSpec.id === "mock") {
          return (
            <label className="flex items-start gap-2 cursor-pointer mt-3 p-3 rounded border border-border bg-surface">
              <input
                type="checkbox"
                checked={mockEnabled}
                onChange={(e) => setMockEnabled(e.target.checked)}
                className="mt-0.5 accent-accent"
              />
              <span>
                <span className="block text-sm">Enabled</span>
                <span className="block text-[11px] text-muted">
                  Offline canned replies. When disabled, Mock is hidden from pickers and unconfigured providers report an error instead.
                </span>
              </span>
            </label>
          );
        }
        if (activeSpec.uses_github_token) {
          return (
            <p className="text-[11px] text-muted mt-2">
              Authenticates with your GitHub token (configured in
              Settings → GitHub, or your <code>gh auth login</code>{" "}
              session).
            </p>
          );
        }
        if (activeSpec.fields.length === 0) return null;
        const cfg = providerConfig[provider] ?? {};
        const present =
          settings?.llm_providers_present?.[provider] ?? {};
        return (
          <div data-llm-provider-config={provider} className="mt-3 space-y-3 p-3 rounded border border-border bg-surface">
            {activeSpec.fields.map((f) => (
              <div key={f.name}>
                {provider === "azure_foundry" && f.name === "deployment" ? (
                  <DeploymentNamesInput
                    value={cfg[f.name] ?? ""}
                    onChange={(value) =>
                      setProviderConfig((prev) => ({
                        ...prev,
                        [provider]: { ...(prev[provider] ?? {}), [f.name]: value },
                      }))
                    }
                  />
                ) : (
                  <>
                    <label className="block text-xs text-muted mb-1">
                      {f.label}
                      {f.secret && present[f.name] && (
                        clearedSecrets.includes(`${provider}.${f.name}`) ? (
                          <span className="text-amber-500"> (will be removed on save)</span>
                        ) : (
                          <span className="text-green-500"> (configured)</span>
                        )
                      )}
                      {f.secret && present[f.name] && (
                        <button
                          type="button"
                          onClick={() =>
                            setClearedSecrets((prev) => {
                              const marker = `${provider}.${f.name}`;
                              return prev.includes(marker)
                                ? prev.filter((m) => m !== marker)
                                : [...prev, marker];
                            })
                          }
                          className="ml-2 underline hover:text-fg"
                          aria-label={`${clearedSecrets.includes(`${provider}.${f.name}`) ? "Keep" : "Remove"} saved ${f.label.replace(/ \(optional\)$/, "")}`}
                          data-tooltip={
                            clearedSecrets.includes(`${provider}.${f.name}`)
                              ? "Keep the stored value"
                              : "Delete the stored value when you save"
                          }
                        >
                          {clearedSecrets.includes(`${provider}.${f.name}`) ? "Undo" : "Remove"}
                        </button>
                      )}
                    </label>
                    <input
                      type={f.secret ? "password" : "text"}
                      value={cfg[f.name] ?? ""}
                      onChange={(e) => {
                        const marker = `${provider}.${f.name}`;
                        if (e.target.value) {
                          setClearedSecrets((prev) => prev.filter((m) => m !== marker));
                        }
                        setProviderConfig((prev) => ({
                          ...prev,
                          [provider]: {
                            ...(prev[provider] ?? {}),
                            [f.name]: e.target.value,
                          },
                        }));
                      }}
                      placeholder={
                        f.secret && present[f.name]
                          ? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
                          : f.placeholder
                      }
                      className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                    />
                  </>
                )}
                {f.help && (
                  <p className="text-[11px] text-muted mt-1">{f.help}</p>
                )}
              </div>
            ))}
            <p className="text-[11px] text-muted">
              Secrets are stored server-side and never returned. Leave a
              configured key blank to keep it.
            </p>
          </div>
        );
      })()}

      <div className="flex items-center gap-2 mt-4">
        <button
          type="button"
          onClick={() => void applyProviderSettings()}
          disabled={modelsLoading}
          className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50"
        >
          {modelsLoading ? "Refreshing\u2026" : "Apply & refresh models"}
        </button>
        <span className="text-[11px] text-muted">
          {modelsError
            ? `Catalog unavailable: ${modelsError}.`
            : models.length > 0
              ? `${models.length} models`
              : "No catalog for this provider."}
        </span>
      </div>
      <p className="text-[11px] text-muted mt-2">
        Configure several providers without changing the global default.
        Chat and topic model pickers list all configured providers and save
        the provider with your model choice, effort and context size.
        Use global default removes that override. Group models from any
        provider into ordered fallbacks in Settings &gt; Presets.
      </p>

      {sys && (
        <div className="mt-6 space-y-3">
          <h3 className="text-sm font-medium">Prompt budgeting</h3>
          <p className="text-[11px] text-muted">
            Caps how much of the transcript is sent to the model so a
            few large reads / tool results can't overflow the context
            window. Lower these for smaller models.
          </p>
          <div>
            <label className="block text-xs text-muted mb-1">
              Max input tokens per request
            </label>
            <NumberInput
              value={sys.llm_max_input_tokens}
              min={1000}
              max={5_000_000}
              onCommit={(n) =>
                setSys({ ...sys, llm_max_input_tokens: n })
              }
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">
              Max tokens per tool result
            </label>
            <NumberInput
              value={sys.llm_max_tool_result_tokens}
              min={100}
              max={2_000_000}
              onCommit={(n) =>
                setSys({ ...sys, llm_max_tool_result_tokens: n })
              }
            />
            <p className="text-[11px] text-muted mt-1">
              Per-message ceiling applied to individual tool outputs
              before they enter the transcript.
            </p>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">
              Keep tool results in full for the last N turns
            </label>
            <NumberInput
              value={sys.llm_tool_result_keep_turns}
              min={0}
              max={1000}
              onCommit={(n) =>
                setSys({ ...sys, llm_tool_result_keep_turns: n })
              }
            />
            <p className="text-[11px] text-muted mt-1">
              Larger tool outputs from older turns reach the model as
              a short preview; it can call the tool again when it
              needs the rest. 0 always sends them in full.
            </p>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">
              Max characters per attached document
            </label>
            <NumberInput
              value={sys.llm_max_attachment_chars}
              min={1000}
              max={2_000_000}
              onCommit={(n) =>
                setSys({ ...sys, llm_max_attachment_chars: n })
              }
            />
            <p className="text-[11px] text-muted mt-1">
              Text extracted from each attached PDF, DOCX, PPTX or
              text file. Longer documents are cut, and the model is
              told how much it can't see.
            </p>
          </div>
        </div>
      )}

      <OpenAIProxySettings
        settings={settings}
        onSettings={(next) => {
          setSettings(next);
          settingsStore.set(next);
        }}
        models={
          provider === settings?.llm_provider && !modelsError ? models : null
        }
      />
    </section>
  );
}
