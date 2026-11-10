import { Select } from "../Select";
import { STT_LANGUAGES } from "./panelConfig";
import type { Settings } from "../../lib/types";
import type { SpeechAuth, SpeechSettings, SttTestState } from "./useSpeechSettings";

export function SpeechTab({ s, settings }: { s: SpeechSettings; settings: Settings | null }) {
  const {
    azureEndpoint,
    setAzureEndpoint,
    azureLanguage,
    setAzureLanguage,
    azureAuth,
    setAzureAuth,
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
        className={INPUT_CLASS}
      />
      <p className="text-[11px] text-muted mt-1">
        The resource endpoint URL from your Azure Speech / Cognitive
        Services resource ("Keys and Endpoint"). Azure CLI sign-in needs
        its custom-domain endpoint, not a regional one.
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

      <label className="block text-xs text-muted mt-4 mb-1">Sign in with</label>
      <Select
        value={azureAuth}
        onChange={(v) => {
          setAzureAuth(v as SpeechAuth);
          setSttTest({ state: "idle" });
        }}
        options={SPEECH_AUTH_OPTIONS}
        ariaLabel="Azure Speech sign-in"
        fullWidth
      />

      {azureAuth === "azure_cli" ? (
        <CliSignInFields s={s} />
      ) : (
        <KeyField
          value={azureKey}
          present={Boolean(keyPresent)}
          onChange={(v) => {
            setAzureKey(v);
            setSttTest({ state: "idle" });
          }}
        />
      )}

      <SttTestRow
        state={sttTest}
        disabled={!azureEndpoint}
        onTest={() => void testStt()}
      />
    </section>
  );
}

const SPEECH_AUTH_OPTIONS: ReadonlyArray<{ value: SpeechAuth; label: string }> = [
  { value: "key", label: "Subscription key" },
  { value: "azure_cli", label: "Azure CLI (az login)" },
];

const INPUT_CLASS =
  "w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent";

function KeyField({
  value,
  present,
  onChange,
}: {
  value: string;
  present: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <>
      <label className="block text-xs text-muted mt-4 mb-1">
        Subscription key{" "}
        {present && <span className="text-green-500">(configured)</span>}
      </label>
      <input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={present ? "••••••••••••••••" : "Azure Speech key"}
        className={INPUT_CLASS}
      />
      <p className="text-[11px] text-muted mt-1">
        Stored server-side and never returned. The browser only ever
        receives a short-lived token minted from it. Leave blank to keep
        the saved key.
      </p>
    </>
  );
}

function CliSignInFields({ s }: { s: SpeechSettings }) {
  const { azureTenant, setAzureTenant, azureResourceId, setAzureResourceId, setSttTest } = s;
  return (
    <>
      <p className="text-[11px] text-muted mt-1">
        Uses the account signed in with <code>az login</code> on the machine
        running Precursor. It needs the <em>Cognitive Services Speech User</em>{" "}
        role on the resource; the browser only receives a short-lived token.
      </p>

      <label className="block text-xs text-muted mt-4 mb-1">Tenant ID (optional)</label>
      <input
        type="text"
        value={azureTenant}
        onChange={(e) => {
          setAzureTenant(e.target.value);
          setSttTest({ state: "idle" });
        }}
        placeholder="CLI default tenant"
        className={INPUT_CLASS}
      />
      <p className="text-[11px] text-muted mt-1">
        Tenant ID or domain to sign in to. Leave blank for the Azure CLI's
        current tenant.
      </p>

      <label className="block text-xs text-muted mt-4 mb-1">Resource ID (optional)</label>
      <input
        type="text"
        value={azureResourceId}
        onChange={(e) => {
          setAzureResourceId(e.target.value);
          setSttTest({ state: "idle" });
        }}
        placeholder="Found from the endpoint"
        className={INPUT_CLASS}
      />
      <p className="text-[11px] text-muted mt-1">
        /subscriptions/…/providers/Microsoft.CognitiveServices/accounts/&lt;name&gt;.
        Leave blank to look it up with Azure Resource Graph from the
        endpoint's custom domain.
      </p>
    </>
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
