import { Select } from "../Select";
import { STT_LANGUAGES } from "./panelConfig";
import type { Settings } from "../../lib/types";
import type { SpeechSettings, SttTestState } from "./useSpeechSettings";

export function SpeechTab({ s, settings }: { s: SpeechSettings; settings: Settings | null }) {
  const {
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
  const keyPresent = settings?.api_keys_present?.azure_speech_key;
  return (
    <section>
      <p className="text-sm text-muted mb-3">
        Dictate prompts with the mic button in the chat composer.
        Configure an <strong>Azure AI Speech</strong> resource to
        enable speech-to-text. Without it, the mic button is hidden.
      </p>
      <SpeechStatus ready={settings?.stt_azure_ready ?? false} />

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
        {keyPresent && (
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
          keyPresent
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

      <SttTestRow
        state={sttTest}
        disabled={!azureEndpoint}
        onTest={() => void testStt()}
      />
    </section>
  );
}

function SpeechStatus({ ready }: { ready: boolean }) {
  return (
    <div
      className={`mb-4 text-[11px] px-2 py-1.5 rounded border ${
        ready ? "border-green-600/40 text-green-500" : "border-border text-muted"
      }`}
    >
      {ready
        ? "Azure Speech is configured — dictation is enabled."
        : "Azure Speech not configured — the mic button is hidden."}
    </div>
  );
}

function SttTestRow({
  state,
  disabled,
  onTest,
}: {
  state: SttTestState;
  disabled: boolean;
  onTest: () => void;
}) {
  return (
    <div className="flex items-center gap-2 mt-3">
      <button
        type="button"
        onClick={onTest}
        disabled={disabled || state.state === "testing"}
        className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50"
      >
        {state.state === "testing" ? "Testing…" : "Test connection"}
      </button>
      {state.state === "ok" && (
        <span className="text-[11px] text-green-500">
          {state.detail ?? "Connection OK."}
        </span>
      )}
      {state.state === "error" && (
        <span className="text-[11px] text-red-500">
          {state.detail ?? "Test failed."}
        </span>
      )}
    </div>
  );
}
