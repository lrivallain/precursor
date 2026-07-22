import { useState } from "react";
import { LogIn, ShieldAlert, X } from "lucide-react";
import { mcpAuthStore, useMcpAuthNotice } from "../lib/mcpAuth";
import { signInWorkiq } from "../lib/workiqSignIn";

/**
 * App-global banner shown when a background MCP connect needs an interactive
 * sign-in (e.g. expired WorkIQ OAuth). Drives the same browser flow as the
 * Settings panel so the user can re-authenticate without leaving their work.
 */
export function McpAuthBanner() {
  const notice = useMcpAuthNotice();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!notice) return null;

  const label = notice.server === "workiq" ? "WorkIQ" : notice.server;

  async function signIn(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      // Opens a script-openable popup synchronously, then blocks until the
      // browser sign-in completes; on success the stale notice is gone and the
      // next turn reuses the fresh session. Resolves null when the user abandons
      // the sign-in (closes the popup) — leave the banner up, no error.
      const status = await signInWorkiq();
      if (status) mcpAuthStore.clear();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-amber-500/10 text-sm">
      <ShieldAlert size={16} className="text-amber-500 shrink-0" />
      <span className="flex-1 min-w-0">
        <span className="font-medium">{label} needs you to sign in</span>
        {error ? (
          <span className="text-red-500"> — {error}</span>
        ) : (
          <span className="text-muted"> to use its tools.</span>
        )}
      </span>
      <button
        onClick={() => void signIn()}
        disabled={busy}
        className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
        data-tooltip="Open the browser sign-in to refresh credentials"
      >
        <LogIn size={13} />
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <button
        onClick={() => mcpAuthStore.clear()}
        disabled={busy}
        aria-label="Dismiss"
        data-tooltip="Dismiss"
        className="p-1 rounded text-muted hover:text-text disabled:opacity-50"
      >
        <X size={14} />
      </button>
    </div>
  );
}
