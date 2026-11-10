import { useState } from "react";
import { api } from "../../lib/api";
import type { Settings } from "../../lib/types";
import type { SettingsSection } from "./settingsSection";

export type SpeechAuth = Settings["azure_speech_auth"];

export type SttTestState = { state: "idle" | "testing" | "ok" | "error"; detail?: string };

/** Speech-to-text tab: the Azure Speech resource, how to sign in, and a connection test. */
export function useSpeechSettings() {
  const [azureEndpoint, setAzureEndpoint] = useState("");
  const [azureLanguage, setAzureLanguage] = useState("");
  const [azureAuth, setAzureAuth] = useState<SpeechAuth>("key");
  const [azureTenant, setAzureTenant] = useState("");
  const [azureResourceId, setAzureResourceId] = useState("");
  // Write-only: the server never returns the key, so "" keeps the saved one.
  const [azureKey, setAzureKey] = useState("");
  const [sttTest, setSttTest] = useState<SttTestState>({ state: "idle" });

  async function testStt(): Promise<void> {
    setSttTest({ state: "testing" });
    try {
      const res = await api.stt.testConnection({
        endpoint: azureEndpoint,
        key: azureKey || null,
        auth: azureAuth,
        tenant: azureTenant,
        resource_id: azureResourceId,
      });
      setSttTest({ state: res.ok ? "ok" : "error", detail: res.detail ?? undefined });
    } catch (e) {
      setSttTest({ state: "error", detail: e instanceof Error ? e.message : "Test failed" });
    }
  }

  const section: SettingsSection = {
    hydrate: (s) => {
      setAzureEndpoint(s.azure_speech_endpoint);
      setAzureLanguage(s.azure_speech_language);
      setAzureAuth(s.azure_speech_auth);
      setAzureTenant(s.azure_speech_tenant);
      setAzureResourceId(s.azure_speech_resource_id);
    },
    payload: () => ({
      azure_speech_endpoint: azureEndpoint,
      azure_speech_language: azureLanguage,
      azure_speech_auth: azureAuth,
      azure_speech_tenant: azureTenant,
      azure_speech_resource_id: azureResourceId,
      ...(azureKey ? { api_keys: { azure_speech_key: azureKey } } : {}),
    }),
    snapshot: [azureEndpoint, azureLanguage, azureAuth, azureTenant, azureResourceId, azureKey],
    onSaved: () => {
      setAzureKey((current) => (current === azureKey ? "" : current));
      return [azureEndpoint, azureLanguage, azureAuth, azureTenant, azureResourceId, ""];
    },
  };
  return {
    azureEndpoint, setAzureEndpoint,
    azureLanguage, setAzureLanguage,
    azureAuth, setAzureAuth,
    azureTenant, setAzureTenant,
    azureResourceId, setAzureResourceId,
    azureKey, setAzureKey,
    sttTest, setSttTest, testStt,
    section,
  };
}

export type SpeechSettings = ReturnType<typeof useSpeechSettings>;
