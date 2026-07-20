import { useSyncExternalStore } from "react";
import { signInWorkiqSilent } from "./workiqSignIn";

/**
 * App-global notice that an MCP server needs an interactive sign-in.
 *
 * Background connects (chat turns, workspace runs) never pop a browser — they
 * surface a ``needs_auth`` state and stream an ``mcp_auth_required`` event. Any
 * SSE consumer reports it here so a single banner can offer an inline
 * re-authenticate action from the main app, without a Settings detour.
 *
 * For WorkIQ we first try a *hands-free* silent re-auth (an invisible
 * ``prompt=none`` iframe): if the browser still holds a live Entra SSO session
 * the banner is cleared with zero clicks. Only when that can't complete does the
 * banner surface for a manual sign-in.
 */
export interface McpAuthNotice {
  server: string;
  message: string;
}

class McpAuthStore {
  private notice: McpAuthNotice | null = null;
  private listeners = new Set<() => void>();
  // Guards the hands-free silent re-auth so we attempt it at most once per
  // notice "episode" (reset on clear), even if several SSE consumers report it.
  private autoReauthTried = false;
  // While a hands-free silent re-auth is running we keep the banner hidden: it
  // usually resolves in a beat, so surfacing (then yanking) the "Sign in" prompt
  // would just flicker — and a click on it would race the invisible iframe for
  // the single loopback port.
  private silentInFlight = false;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  };

  getSnapshot = (): McpAuthNotice | null => (this.silentInFlight ? null : this.notice);

  report(server: string, message: string): void {
    const changed =
      !this.notice || this.notice.server !== server || this.notice.message !== message;
    if (changed) this.notice = { server, message };
    this.maybeAutoReauth(server);
    if (changed) this.emit();
  }

  clear(): void {
    this.autoReauthTried = false;
    this.silentInFlight = false;
    if (this.notice === null) return;
    this.notice = null;
    this.emit();
  }

  /**
   * Kick off the one-shot hands-free silent WorkIQ re-auth for a fresh notice.
   *
   * Keeps the banner hidden while the invisible ``prompt=none`` iframe runs; on
   * success the notice is cleared so the banner never appears; otherwise it is
   * revealed for a manual "Sign in". A no-op for non-WorkIQ servers or once an
   * attempt has already run for the current episode.
   */
  private maybeAutoReauth(server: string): void {
    if (server !== "workiq" || this.autoReauthTried) return;
    this.autoReauthTried = true;
    this.silentInFlight = true;
    void signInWorkiqSilent().then((authenticated) => {
      this.silentInFlight = false;
      if (authenticated) {
        this.clear();
      } else {
        // Silent pass needs a human — reveal the manual sign-in banner.
        this.emit();
      }
    });
  }

  private emit(): void {
    for (const cb of this.listeners) cb();
  }
}

export const mcpAuthStore = new McpAuthStore();

export function useMcpAuthNotice(): McpAuthNotice | null {
  return useSyncExternalStore(
    mcpAuthStore.subscribe,
    mcpAuthStore.getSnapshot,
    mcpAuthStore.getSnapshot,
  );
}
