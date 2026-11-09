import { useState } from "react";
import { api } from "../../lib/api";
import { modelsStore } from "../../lib/modelsStore";
import type { LLMProviderSpec } from "../../lib/types";
import {
  buildLlmProvidersPayload,
  defaultModelPayload,
  pinnedDefaultModel,
  secretMarker,
  toggleMarker,
  type ProviderConfig,
} from "./modelSettings";
import type { SettingsSection } from "./settingsSection";
import { useModelCatalog, useProviderModels } from "./useModelCatalog";
import type { LoadedSettings } from "./useSettingsForm";

/**
 * Model tab: the global default provider and model, and the configuration of
 * each provider. The catalog it loads also feeds the Chat and Live pickers.
 */
export function useModelSettings({ settings, adopt }: LoadedSettings) {
  const catalog = useModelCatalog();
  const { loadModels, setModelsError, setModelsLoading } = catalog;
  const [providers, setProviders] = useState<LLMProviderSpec[]>([]);
  // The provider being configured, independent of the global default.
  const [provider, setProvider] = useState("");
  const [defaultProvider, setDefaultProvider] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const defaultModels = useProviderModels(defaultProvider);
  const [mockEnabled, setMockEnabled] = useState(true);
  const [providerConfig, setProviderConfig] = useState<ProviderConfig>({});
  // Stored provider secrets ("provider.field") queued for removal on save. The
  // server never returns secrets, so an empty input alone can't mean "clear".
  const [clearedSecrets, setClearedSecrets] = useState<string[]>([]);

  // Switching providers reloads the catalog for the newly selected provider
  // (previewed via the override param, using its saved config) so the model
  // list and selection always match the chosen provider.
  function onProviderChange(next: string): void {
    setProvider(next);
    void loadModels(next);
  }

  function chooseDefaultProvider(next: string): void {
    setDefaultProvider(next);
    setDefaultModel("");
  }

  // Typing a value for a secret queued for removal cancels the removal.
  function editField(field: string, value: string): void {
    if (value) setClearedSecrets((prev) => prev.filter((m) => m !== secretMarker(provider, field)));
    setProviderConfig((prev) => ({ ...prev, [provider]: { ...(prev[provider] ?? {}), [field]: value } }));
  }

  function toggleClearedSecret(field: string): void {
    setClearedSecrets((prev) => toggleMarker(prev, secretMarker(provider, field)));
  }

  const providersPayload = () => buildLlmProvidersPayload(providers, providerConfig, clearedSecrets);

  // Persist provider config without changing the default, then refresh the
  // catalog — without closing the panel. The server resolves retired pins;
  // don't overwrite a categorised pin merely because it is absent from the
  // refreshed catalogue.
  async function applyProviderSettings(): Promise<void> {
    setModelsLoading(true);
    try {
      const updated = await api.settings.update({ llm_providers: providersPayload() });
      adopt(updated);
      setProviderConfig(updated.llm_providers ?? {});
      setClearedSecrets([]);
      await loadModels(provider, { share: true });
      modelsStore.applySettings(updated);
    } catch (e) {
      setModelsError(e instanceof Error ? e.message : String(e));
      setModelsLoading(false);
    }
  }

  const section: SettingsSection = {
    hydrate: (s) => {
      setProvider(s.llm_provider);
      setDefaultProvider(s.llm_provider);
      setDefaultModel(pinnedDefaultModel(s));
      setMockEnabled(s.mock_provider_enabled ?? true);
      setProviderConfig(s.llm_providers ?? {});
    },
    load: async (s) => {
      try {
        setProviders(await api.llm.listProviders(true));
      } catch {
        setProviders([]);
      }
      await loadModels(s.llm_provider, { share: true });
    },
    payload: () => {
      const llmProviders = providersPayload();
      return {
        llm_provider: defaultProvider,
        mock_provider_enabled: mockEnabled,
        ...defaultModelPayload(settings, defaultProvider, defaultModel),
        ...(Object.keys(llmProviders).length > 0 ? { llm_providers: llmProviders } : {}),
      };
    },
    snapshot: [defaultProvider, defaultModel, mockEnabled, providerConfig, clearedSecrets],
    onSaved: (updated, sent) => {
      for (const configured of Object.keys(sent.llm_providers ?? {})) void modelsStore.refresh(configured);
      const savedConfig = updated.llm_providers ?? {};
      const savedModel = pinnedDefaultModel(updated);
      setProviderConfig((current) => (current === providerConfig ? savedConfig : current));
      setClearedSecrets((current) => (current === clearedSecrets ? [] : current));
      setDefaultModel((current) => (current === defaultModel ? savedModel : current));
      return [defaultProvider, savedModel, mockEnabled, savedConfig, []];
    },
  };

  return {
    ...catalog,
    settings,
    adopt,
    providers,
    provider,
    onProviderChange,
    defaultProvider,
    chooseDefaultProvider,
    defaultModel,
    setDefaultModel,
    defaultModels,
    mockEnabled,
    setMockEnabled,
    providerConfig,
    clearedSecrets,
    editField,
    toggleClearedSecret,
    applyProviderSettings,
    /** Saved settings with the unsaved Mock switch, for the setup badges. */
    liveSettings: settings ? { ...settings, mock_provider_enabled: mockEnabled } : settings,
    activeProviderSpec: providers.find((p) => p.id === provider),
    section,
  };
}

export type ModelSettings = ReturnType<typeof useModelSettings>;
