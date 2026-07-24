import { useSyncExternalStore } from "react";
import { autoReauthWorkiq } from "./workiqSignIn";

/**
 * App-global notice that an MCP server needs an interactive sign-in.
 *
 * Background connects (chat turns, workspace runs) never pop a browser — they
 * surface a ``needs_auth`` state and stream an ``mcp_auth_required`` event. Any
 * SSE consumer reports it here so a single banner can offer an inline
 * re-authenticate action from the main app, without a Settings detour.
 *
 * For WorkIQ we first try a *hands-free, self-triggering* re-auth: the backend
 * runs an invisible ``prompt=none`` iframe pass and, if that needs interaction,
 * self-opens the OS browser to the visible prompt — so a live SSO session (or a
 * quick sign-in) clears the banner with no click. The banner only surfaces when
 * even that can't complete (auto re-auth off, port busy, declined).
 */
export interface McpAuthNotice {
  server: string;
  message: string;
}

class McpAuthStore {
  private notice: McpAuthNotice | null = null;
  private listeners = new Set<() => void>();
  // Guards the hands-free self-triggering re-auth so we attempt it at most once
  // per notice "episode" (reset on clear), even if several SSE consumers report
  // it.
  private autoReauthTried = false;
  // While the hands-free re-auth is running we keep the banner hidden: the
  // silent pass usually resolves in a beat, and when it self-opens the OS browser
  // the user is already signing in there — surfacing (then yanking) a "Sign in"
  // prompt would just flicker, and a click on it would race the flow for the
  // single loopback port.
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
   * Kick off the one-shot hands-free self-triggering WorkIQ re-auth for a fresh
   * notice.
   *
   * Keeps the banner hidden while the backend runs the invisible ``prompt=none``
   * iframe pass and, if needed, self-opens the OS browser to the visible prompt;
   * on success the notice is cleared so the banner never appears; otherwise it is
   * revealed for a manual "Sign in". A no-op for non-WorkIQ servers or once an
   * attempt has already run for the current episode.
   */
  private maybeAutoReauth(server: string): void {
    if (server !== "workiq" || this.autoReauthTried) return;
    this.autoReauthTried = true;
    this.silentInFlight = true;
    void autoReauthWorkiq().then((authenticated) => {
      this.silentInFlight = false;
      if (authenticated) {
        this.clear();
      } else {
        // Couldn't complete hands-free — reveal the manual sign-in banner.
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
