import { providerFieldValue, shownProviderFields } from "../../lib/providerFields";
import type { LLMProviderField } from "../../lib/types";
import { DeploymentNamesInput } from "../DeploymentNamesInput";
import { Select } from "../Select";
import { secretMarker } from "./modelSettings";
import type { ModelSettings } from "./useModelSettings";

const SECRET_PLACEHOLDER = "•".repeat(12);

/** The setup of the provider picked under "Configure provider". */
export function ProviderSetup({ m }: { m: ModelSettings }) {
  const spec = m.activeProviderSpec;
  if (!spec) return null;
  if (spec.id === "mock") {
    return (
      <label className="flex items-start gap-2 cursor-pointer mt-3 p-3 rounded border border-border bg-surface">
        <input
          type="checkbox"
          checked={m.mockEnabled}
          onChange={(e) => m.setMockEnabled(e.target.checked)}
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
  if (spec.uses_github_token) {
    return (
      <p className="text-[11px] text-muted mt-2">
        Authenticates with your GitHub token (configured in
        Settings → GitHub, or your <code>gh auth login</code>{" "}
        session).
      </p>
    );
  }
  if (spec.fields.length === 0) return null;
  return (
    <div data-llm-provider-config={m.provider} className="mt-3 space-y-3 p-3 rounded border border-border bg-surface">
      {shownProviderFields(spec.fields, m.providerConfig[m.provider]).map((f) => (
        <div key={f.name}>
          <ProviderField m={m} field={f} />
          {f.help && <p className="text-[11px] text-muted mt-1">{f.help}</p>}
        </div>
      ))}
      <p className="text-[11px] text-muted">
        Secrets are stored server-side and never returned. Leave a
        configured key blank to keep it.
      </p>
    </div>
  );
}

function ProviderField({ m, field: f }: { m: ModelSettings; field: LLMProviderField }) {
  const { provider } = m;
  const value = providerFieldValue(f, m.providerConfig[provider]);
  if (provider === "azure_foundry" && f.name === "deployment") {
    return <DeploymentNamesInput value={value} onChange={(next) => m.editField(f.name, next)} />;
  }
  if (f.choices.length > 0) {
    return (
      <>
        <label className="block text-xs text-muted mb-1">{f.label}</label>
        <Select
          value={value}
          onChange={(next) => m.editField(f.name, next)}
          options={f.choices}
          ariaLabel={f.label}
          fullWidth
        />
      </>
    );
  }
  const stored = f.secret && Boolean(m.settings?.llm_providers_present?.[provider]?.[f.name]);
  return (
    <>
      <label className="block text-xs text-muted mb-1">
        {f.label}
        {stored && <StoredSecretControls m={m} field={f} />}
      </label>
      <input
        type={f.secret ? "password" : "text"}
        value={value}
        onChange={(e) => m.editField(f.name, e.target.value)}
        placeholder={stored ? SECRET_PLACEHOLDER : f.placeholder}
        className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
    </>
  );
}

// A saved secret can't be cleared by emptying its input (blank keeps it), so
// it gets an explicit Remove / Undo toggle.
function StoredSecretControls({ m, field: f }: { m: ModelSettings; field: LLMProviderField }) {
  const removing = m.clearedSecrets.includes(secretMarker(m.provider, f.name));
  return (
    <>
      {removing ? (
        <span className="text-amber-500"> (will be removed on save)</span>
      ) : (
        <span className="text-green-500"> (configured)</span>
      )}
      <button
        type="button"
        onClick={() => m.toggleClearedSecret(f.name)}
        className="ml-2 underline hover:text-fg"
        aria-label={`${removing ? "Keep" : "Remove"} saved ${f.label.replace(/ \(optional\)$/, "")}`}
        data-tooltip={removing ? "Keep the stored value" : "Delete the stored value when you save"}
      >
        {removing ? "Undo" : "Remove"}
      </button>
    </>
  );
}
