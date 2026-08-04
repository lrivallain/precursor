import { useState } from "react";
import { LogIn, ShieldAlert, X } from "lucide-react";
import { mcpAuthStore, useMcpAuthNotices } from "../lib/mcpAuth";
import { signInWorkiq } from "../lib/workiqSignIn";
import { mcpServerLabel } from "../lib/mcpServers";

/**
 * App-global banner shown when a background MCP connect needs an interactive
 * sign-in (e.g. expired WorkIQ OAuth). Drives the same browser flow as the
 * Settings panel so the user can re-authenticate without leaving their work.
 *
 * Several credentials can be stale at once (the hosted WorkIQ preview and the
 * Agent 365 servers are separate Entra clients), so the banner aggregates them
 * into one row rather than stacking a prompt per server. The button only signs
 * in the *first* one: completing it leaves the browser holding a hot Entra SSO
 * cookie, which the store immediately spends on a hands-free pass for the
 * others — so the usual outcome is one click for the whole family.
 */
export function McpAuthBanner() {
  const notices = useMcpAuthNotices();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (notices.length === 0) return null;

  const labels = notices.map((notice) => mcpServerLabel(notice.server));
  const subject =
    labels.length === 1
      ? labels[0]
      : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
  const server = notices[0].server;

  async function signIn(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      // Opens a script-openable popup synchronously, then blocks until the
      // browser sign-in completes; on success the stale notice is gone and the
      // next turn reuses the fresh session. Resolves null when the user abandons
      // the sign-in (closes the popup) — leave the banner up, no error.
      const status = await signInWorkiq(server);
      // Clears this credential and, while its SSO cookie is hot, re-tries the
      // remaining ones hands-free (no second popup, so no user gesture needed).
      if (status) mcpAuthStore.resolve(server);
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
        <span className="font-medium">
          {subject} need{labels.length === 1 ? "s" : ""} you to sign in
        </span>
        {error ? (
          <span className="text-red-500"> — {error}</span>
        ) : (
          <span className="text-muted"> to use {labels.length === 1 ? "its" : "their"} tools.</span>
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
