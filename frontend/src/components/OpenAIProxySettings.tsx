import { useState, type ReactNode } from "react";
import { Check, Copy, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { vscodeLanguageModelsConfig } from "../lib/openaiProxy";
import type { LLMModel, Settings } from "../lib/types";
import { useConfirm } from "./ConfirmDialog";

interface Props {
  settings: Settings | null;
  onSettings: (next: Settings) => void;
  /** Catalogue of the *saved* provider, or null while the picker previews
   *  another one — a VS Code config built from it would list the wrong models. */
  models: LLMModel[] | null;
}

/**
 * Settings → Model card for the OpenAI-compatible endpoint. Unlike the rest of
 * the panel the switch applies immediately: turning it on mints the API key,
 * and the user needs that key on screen to configure a client.
 */
export function OpenAIProxySettings({ settings, onSettings, models }: Props) {
  const confirmAction = useConfirm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  // The switch reflects the click right away instead of waiting on the save.
  const [pending, setPending] = useState<boolean | null>(null);

  if (!settings) return null;
  const current = settings;
  const enabled = current.openai_proxy_enabled;
  const key = current.openai_proxy_key;
  const url = current.openai_proxy_url;
  // An unusable provider degrades to the mock, whose catalogue must not end up
  // in a client's config.
  const catalog = current.openai_proxy_available ? models : null;

  async function toggle(next: boolean): Promise<void> {
    setBusy(true);
    setPending(next);
    setError(null);
    try {
      onSettings(await api.settings.update({ openai_proxy_enabled: next }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
      setBusy(false);
    }
  }

  async function regenerate(): Promise<void> {
    const ok = await confirmAction({
      message:
        "Regenerate the API key? Clients using the current key are refused until you give them the new one.",
      confirmLabel: "Regenerate key",
      variant: "danger",
    });
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      const { key: next } = await api.settings.regenerateOpenAIProxyKey();
      onSettings({ ...current, openai_proxy_key: next });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy(label: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied((c) => (c === label ? null : c)), 1500);
    } catch {
      /* clipboard unavailable — the value stays selectable in its field */
    }
  }

  return (
    <div className="mt-6 space-y-3">
      <h3 className="text-sm font-medium">OpenAI-compatible endpoint</h3>
      <p className="text-[11px] text-muted">
        Let OpenAI-compatible clients — VS Code, Open WebUI, Continue, the{" "}
        <code>openai</code> SDK — use the active provider's models through
        Precursor. Requests are relayed as they are, tools included; their token
        usage shows under Usage stats.
      </p>
      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={pending ?? enabled}
          disabled={busy}
          onChange={(e) => void toggle(e.target.checked)}
          className="mt-0.5 accent-accent"
        />
        <span className="min-w-0">
          <span className="block text-sm">Serve an OpenAI-compatible endpoint</span>
          <span className="block text-[11px] text-muted">
            Applies immediately. Clients authenticate with the API key below.
          </span>
        </span>
      </label>

      {!current.openai_proxy_available && (
        <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
          Unavailable with the current provider:{" "}
          {current.openai_proxy_unavailable_reason}
        </div>
      )}

      {enabled && key && (
        <div className="p-3 rounded border border-border bg-surface space-y-3">
          <CopyField
            label="Base URL"
            value={url}
            copied={copied === "url"}
            onCopy={() => void copy("url", url)}
          />
          <CopyField
            label="API key"
            value={key}
            copied={copied === "key"}
            onCopy={() => void copy("key", key)}
          >
            <button
              type="button"
              onClick={() => void regenerate()}
              disabled={busy}
              aria-label="Regenerate API key"
              data-tooltip="Regenerate key"
              className="p-1.5 rounded border border-border hover:bg-bg disabled:opacity-50"
            >
              <RefreshCw size={14} />
            </button>
          </CopyField>
          <p className="text-[11px] text-muted">
            The key stays readable here so you can set up more clients later —
            anyone who can open Precursor's settings can read it too.
          </p>

          <div className="border-t border-border pt-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() =>
                  catalog && void copy("vscode", vscodeLanguageModelsConfig(url, key, catalog))
                }
                disabled={!catalog || catalog.length === 0}
                className="px-3 py-1.5 rounded text-xs border border-border hover:bg-bg disabled:opacity-50 inline-flex items-center gap-1.5"
              >
                {copied === "vscode" ? <Check size={12} /> : <Copy size={12} />}
                Copy VS Code model config
              </button>
              <span className="text-[11px] text-muted">
                {!current.openai_proxy_available
                  ? "Needs a usable provider."
                  : catalog === null
                    ? "Apply the provider to list its models."
                    : `${catalog.length} models`}
              </span>
            </div>
            <p className="text-[11px] text-muted mt-1">
              In VS Code, run <strong>Chat: Manage Language Models</strong> →{" "}
              <strong>Add Models</strong> → <strong>Custom Endpoint</strong>, then
              put this in the <code>chatLanguageModels.json</code> it opens, in
              place of the entry it just added. Drop the models you don't want in
              the picker.
            </p>
          </div>
        </div>
      )}

      {enabled && current.llm_provider === "github_copilot" && (
        <div className="text-[11px] rounded border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-2">
          Requests reach GitHub Copilot with your account. Copilot isn't meant to
          be proxied: heavy or automated use through this endpoint can trip
          GitHub's abuse detection and suspend your Copilot access. Keep it to
          interactive use.
        </div>
      )}

      {error && <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}

function CopyField({
  label,
  value,
  copied,
  onCopy,
  children,
}: {
  label: string;
  value: string;
  copied: boolean;
  onCopy: () => void;
  children?: ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs text-muted mb-1">{label}</label>
      <div className="flex items-center gap-1.5">
        <input
          readOnly
          value={value}
          aria-label={label}
          onFocus={(e) => e.currentTarget.select()}
          className="flex-1 min-w-0 bg-bg border border-border rounded px-2 py-1.5 text-xs font-mono outline-none focus:border-accent"
        />
        <button
          type="button"
          onClick={onCopy}
          aria-label={`Copy ${label.toLowerCase()}`}
          data-tooltip={copied ? "Copied" : "Copy"}
          className="p-1.5 rounded border border-border hover:bg-bg"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        {children}
      </div>
    </div>
  );
}
