import { useRef, useState } from "react";
import { Search } from "lucide-react";
import { api } from "../../lib/api";
import { useBusyAction } from "../../lib/useBusyAction";
import type { MCPServerAuth } from "../../lib/types";
import { applyAuthDetection, azLoginCommand, type McpServerForm } from "./mcpServerForm";

const INPUT = "w-full bg-surface border border-border rounded px-2 py-1.5 text-sm font-mono outline-none focus:border-accent";

type Update = (patch: Partial<McpServerForm>) => void;

/**
 * Probes the server URL for the sign-in it advertises and fills the auth
 * fields. `detectOnce` runs it at most once per URL, so leaving the URL field
 * detects automatically without re-probing a URL already checked.
 */
export function useAuthDetection(form: McpServerForm, update: Update) {
  const { busy, run } = useBusyAction();
  const [message, setMessage] = useState<string | null>(null);
  const checked = useRef<string | null>(null);

  async function detect(): Promise<void> {
    const url = form.url.trim();
    if (!url) return;
    checked.current = url;
    await run(async () => {
      try {
        const detection = await api.mcp.detectAuth(url);
        setMessage(detection.message);
        update(applyAuthDetection(form, detection));
      } catch (e) {
        setMessage(`Detection failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  }

  function detectOnce(): void {
    if (form.url.trim() && checked.current !== form.url.trim()) void detect();
  }

  return { busy, message, detect, detectOnce };
}

export function DetectAuthButton({ busy, onClick, disabled }: { busy: boolean; onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy || disabled}
      data-tooltip="Ask the server which sign-in it expects and fill in the authentication"
      className="flex items-center gap-1 px-2 py-1.5 rounded border border-border text-xs text-muted hover:text-text whitespace-nowrap disabled:opacity-50"
    >
      <Search size={12} />
      {busy ? "Detecting..." : "Detect"}
    </button>
  );
}

export function AuthFields({ form, update, message }: { form: McpServerForm; update: Update; message: string | null }) {
  const option = (auth: MCPServerAuth, label: string) => (
    <label className="flex items-center gap-1 cursor-pointer">
      <input
        type="radio"
        name="auth"
        checked={form.auth === auth}
        onChange={() => update({ auth })}
        className="accent-accent"
      />
      {label}
    </label>
  );
  return (
    <div className="space-y-2">
      <div>
        <label className="block text-xs text-muted mb-1">Authentication</label>
        <div className="flex gap-3 text-xs">
          {option("none", "None / headers")}
          {option("azure_cli", "Entra ID (Azure CLI)")}
        </div>
        {message && <p className="text-[10px] text-muted mt-1">{message}</p>}
      </div>
      {form.auth === "azure_cli" && <AzureCliFields form={form} update={update} />}
    </div>
  );
}

function AzureCliFields({ form, update }: { form: McpServerForm; update: Update }) {
  return (
    <div className="space-y-2 border-l-2 border-border pl-2">
      <div>
        <label className="block text-xs text-muted mb-1">Tenant</label>
        <input
          type="text"
          value={form.authTenant}
          onChange={(e) => update({ authTenant: e.target.value })}
          placeholder="Azure CLI default tenant"
          className={INPUT}
        />
      </div>
      <div>
        <label className="block text-xs text-muted mb-1">Resource (application ID URI)</label>
        <input
          type="text"
          value={form.authResource}
          onChange={(e) => update({ authResource: e.target.value })}
          placeholder="api://00000000-0000-0000-0000-000000000000"
          className={INPUT}
        />
      </div>
      <p className="text-[10px] text-muted">
        Precursor asks your Azure CLI for a token, so no extra sign-in is needed once you are
        signed in. If the server reports a sign-in error, run this once, then reload it:
      </p>
      <code className="block text-[10px] font-mono bg-surface border border-border rounded px-2 py-1 break-all select-all">
        {azLoginCommand(form.authTenant, form.authResource)}
      </code>
    </div>
  );
}
