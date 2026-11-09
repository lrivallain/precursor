import { useState } from "react";
import { api } from "../../lib/api";
import type { SettingsSection } from "./settingsSection";

export type SttTestState = { state: "idle" | "testing" | "ok" | "error"; detail?: string };

/** Speech-to-text tab: the Azure Speech resource and a connection test. */
export function useSpeechSettings() {
  const [azureEndpoint, setAzureEndpoint] = useState("");
  const [azureLanguage, setAzureLanguage] = useState("");
  // Write-only: the server never returns the key, so "" keeps the saved one.
  const [azureKey, setAzureKey] = useState("");
  const [sttTest, setSttTest] = useState<SttTestState>({ state: "idle" });

  async function testStt(): Promise<void> {
    setSttTest({ state: "testing" });
    try {
      const res = await api.stt.testConnection(azureEndpoint, azureKey);
      setSttTest({ state: res.ok ? "ok" : "error", detail: res.detail ?? undefined });
    } catch (e) {
      setSttTest({ state: "error", detail: e instanceof Error ? e.message : "Test failed" });
    }
  }

  const section: SettingsSection = {
    hydrate: (s) => {
      setAzureEndpoint(s.azure_speech_endpoint);
      setAzureLanguage(s.azure_speech_language);
    },
    payload: () => ({
      azure_speech_endpoint: azureEndpoint,
      azure_speech_language: azureLanguage,
      ...(azureKey ? { api_keys: { azure_speech_key: azureKey } } : {}),
    }),
    snapshot: [azureEndpoint, azureLanguage, azureKey],
    onSaved: () => {
      setAzureKey((current) => (current === azureKey ? "" : current));
      return [azureEndpoint, azureLanguage, ""];
    },
  };
  return {
    azureEndpoint, setAzureEndpoint,
    azureLanguage, setAzureLanguage,
    azureKey, setAzureKey,
    sttTest, setSttTest, testStt,
    section,
  };
}

export type SpeechSettings = ReturnType<typeof useSpeechSettings>;
