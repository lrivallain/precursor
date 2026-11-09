import { Select } from "../Select";
import { STT_LANGUAGES } from "./panelConfig";
import type { SettingsPanelState } from "./useSettingsPanelState";

export function SpeechTab({ s }: { s: SettingsPanelState }) {
  const {
    settings,
    azureEndpoint,
    setAzureEndpoint,
    azureLanguage,
    setAzureLanguage,
    azureKey,
    setAzureKey,
    sttTest,
    setSttTest,
    testStt,
  } = s;
  return (
    <section>
      <p className="text-sm text-muted mb-3">
        Dictate prompts with the mic button in the chat composer.
        Configure an <strong>Azure AI Speech</strong> resource to
        enable speech-to-text. Without it, the mic button is hidden.
      </p>
      <div
        className={`mb-4 text-[11px] px-2 py-1.5 rounded border ${
          settings?.stt_azure_ready
            ? "border-green-600/40 text-green-500"
            : "border-border text-muted"
        }`}
      >
        {settings?.stt_azure_ready
          ? "Azure Speech is configured — dictation is enabled."
          : "Azure Speech not configured — the mic button is hidden."}
      </div>

      <label className="block text-xs text-muted mb-1">Endpoint URL</label>
      <input
        type="text"
        value={azureEndpoint}
        onChange={(e) => {
          setAzureEndpoint(e.target.value);
          setSttTest({ state: "idle" });
        }}
        placeholder="https://<name>.cognitiveservices.azure.com/"
        className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
      <p className="text-[11px] text-muted mt-1">
        The resource endpoint URL from your Azure Speech / Cognitive
        Services resource ("Keys and Endpoint").
      </p>

      <label className="block text-xs text-muted mt-4 mb-1">Language</label>
      <Select
        value={azureLanguage}
        onChange={setAzureLanguage}
        options={STT_LANGUAGES}
        ariaLabel="Speech recognition language"
        fullWidth
      />
      <p className="text-[11px] text-muted mt-1">
        Recognition language. "Auto (browser)" uses the browser's
        current locale.
      </p>

      <label className="block text-xs text-muted mt-4 mb-1">
        Subscription key{" "}
        {settings?.api_keys_present?.azure_speech_key && (
          <span className="text-green-500">(configured)</span>
        )}
      </label>
      <input
        type="password"
        value={azureKey}
        onChange={(e) => {
          setAzureKey(e.target.value);
          setSttTest({ state: "idle" });
        }}
        placeholder={
          settings?.api_keys_present?.azure_speech_key
            ? "••••••••••••••••"
            : "Azure Speech key"
        }
        className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
      <p className="text-[11px] text-muted mt-1">
        Stored server-side and never returned. The browser only ever
        receives a short-lived token minted from it. Leave blank to keep
        the saved key.
      </p>

      <div className="flex items-center gap-2 mt-3">
        <button
          type="button"
          onClick={() => void testStt()}
          disabled={!azureEndpoint || sttTest.state === "testing"}
          className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50"
        >
          {sttTest.state === "testing" ? "Testing…" : "Test connection"}
        </button>
        {sttTest.state === "ok" && (
          <span className="text-[11px] text-green-500">
            {sttTest.detail ?? "Connection OK."}
          </span>
        )}
        {sttTest.state === "error" && (
          <span className="text-[11px] text-red-500">
            {sttTest.detail ?? "Test failed."}
          </span>
        )}
      </div>
    </section>
  );
}
