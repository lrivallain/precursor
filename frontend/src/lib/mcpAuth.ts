import { useSyncExternalStore } from "react";
import { autoReauthWorkiq } from "./workiqSignIn";
import { OAUTH_SERVERS, mcpAuthFamily } from "./mcpServers";

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
 *
 * State is keyed by *auth family* (see {@link mcpAuthFamily}), not by server:
 * ``workiq-teams`` and ``workiq-user`` share one credential, so one sign-in
 * covers both, while the hosted WorkIQ preview is a separate Entra client that
 * genuinely needs its own. Tracking families independently is what lets the
 * second credential still get its hands-free pass instead of falling straight
 * through to a manual banner click.
 */
export interface McpAuthNotice {
  server: string;
  message: string;
}

interface FamilyState {
  notice: McpAuthNotice;
  /**
   * Whether the one-shot hands-free re-auth has already run for this notice
   * "episode" (reset when the family clears), so several SSE consumers
   * reporting the same outage don't each trigger a sign-in.
   */
  autoReauthTried: boolean;
  /**
   * While the hands-free re-auth is queued or running we keep this family out
   * of the banner: the silent pass usually resolves in a beat, and when it
   * self-opens the OS browser the user is already signing in there — surfacing
   * (then yanking) a "Sign in" prompt would just flicker, and a click on it
   * would race the flow for the loopback port.
   */
  silentInFlight: boolean;
}

const NO_NOTICES: readonly McpAuthNotice[] = Object.freeze([]);

class McpAuthStore {
  private families = new Map<string, FamilyState>();
  private listeners = new Set<() => void>();
  // Cached derived value: ``useSyncExternalStore`` compares snapshots by
  // identity, so this must only change when the visible notices actually do —
  // rebuilding the array on every read would loop React forever.
  private snapshot: readonly McpAuthNotice[] = NO_NOTICES;
  // Families awaiting their hands-free pass. Drained one at a time: concurrent
  // flows would race for the OAuth authorization URL, which the backend
  // broadcasts without naming its target (see ``emitWorkiqAuthUrl``).
  private queue: string[] = [];
  private draining = false;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  };

  getSnapshot = (): readonly McpAuthNotice[] => this.snapshot;

  report(server: string, message: string): void {
    const family = mcpAuthFamily(server);
    const existing = this.families.get(family);
    if (existing) {
      // Same credential, possibly a different endpoint of it: refresh the text
      // but keep the episode (and its one-shot auto attempt) going.
      existing.notice = { server, message };
    } else {
      this.families.set(family, {
        notice: { server, message },
        autoReauthTried: false,
        silentInFlight: false,
      });
    }
    this.enqueueAutoReauth(server);
    this.emit();
  }

  /** Drop every notice — the user dismissed the banner. */
  clear(): void {
    if (this.families.size === 0) return;
    this.families.clear();
    this.queue = [];
    this.emit();
  }

  /**
   * Drop the notice for ``server``'s credential because its sign-in was renewed.
   *
   * Broadcast over the event bus (``mcp.auth_resolved``) when any window
   * completes an interactive re-auth — via its popup, the OS-browser tab the
   * hands-free flow self-opens, or a silent pass. Windows that only ever saw the
   * ``mcp_auth_required`` notice (and never drove the sign-in themselves) get
   * told the credentials are fresh and clear their stale banner without a
   * reload. Only the matching auth family clears; a sibling credential that is
   * still expired keeps its own notice.
   *
   * A fresh sign-in also leaves the browser holding a hot Entra SSO cookie, so
   * this is the cheapest moment to renew the *other* credentials: any family
   * still pending is re-armed for another hands-free pass, which now usually
   * completes with zero clicks. That turns "one visible prompt per credential"
   * into a single prompt for the whole WorkIQ family.
   */
  resolve(server: string): void {
    const family = mcpAuthFamily(server);
    if (!this.families.delete(family)) return;
    this.chainPendingFamilies();
    this.emit();
  }

  /**
   * Re-arm the hands-free pass for families still pending after a sibling just
   * signed in, while that sign-in's Entra SSO cookie is still hot.
   */
  private chainPendingFamilies(): void {
    for (const state of this.families.values()) {
      if (state.silentInFlight) continue;
      state.autoReauthTried = false;
      this.enqueueAutoReauth(state.notice.server);
    }
  }

  /**
   * Queue the one-shot hands-free self-triggering re-auth for a fresh notice.
   *
   * Keeps the family out of the banner while the backend runs the invisible
   * ``prompt=none`` iframe pass and, if needed, self-opens the OS browser to the
   * visible prompt; on success the notice is dropped so the banner never
   * appears, otherwise it is revealed for a manual "Sign in". A no-op for
   * servers outside the WorkIQ family (which share this OAuth flow) or once an
   * attempt has already run for the current episode.
   */
  private enqueueAutoReauth(server: string): void {
    if (!OAUTH_SERVERS.has(server)) return;
    const family = mcpAuthFamily(server);
    const state = this.families.get(family);
    if (!state || state.autoReauthTried) return;
    state.autoReauthTried = true;
    state.silentInFlight = true;
    this.queue.push(server);
    void this.drain();
  }

  /** Run queued hands-free attempts strictly one at a time. */
  private async drain(): Promise<void> {
    if (this.draining) return;
    this.draining = true;
    try {
      for (let server = this.queue.shift(); server; server = this.queue.shift()) {
        const family = mcpAuthFamily(server);
        let authenticated = false;
        try {
          authenticated = await autoReauthWorkiq(server);
        } catch {
          // A background attempt never surfaces an error; fall back to the banner.
          authenticated = false;
        }
        const state = this.families.get(family);
        // Cleared while we were away (dismissed, or resolved by another window).
        if (!state) continue;
        state.silentInFlight = false;
        if (authenticated) {
          this.families.delete(family);
          // Same reasoning as ``resolve``: the SSO cookie is hot now, so give any
          // other pending family a fresh hands-free shot before it hits the banner.
          this.chainPendingFamilies();
        }
        this.emit();
      }
    } finally {
      this.draining = false;
    }
  }

  private emit(): void {
    const visible = [...this.families.values()]
      .filter((state) => !state.silentInFlight)
      .map((state) => state.notice);
    // Preserve identity when nothing visible changed, so subscribers don't
    // re-render on purely internal transitions (a queued attempt starting, say).
    const unchanged =
      visible.length === this.snapshot.length &&
      visible.every((notice, i) => {
        const prev = this.snapshot[i];
        return prev.server === notice.server && prev.message === notice.message;
      });
    if (unchanged) return;
    this.snapshot = visible.length === 0 ? NO_NOTICES : visible;
    for (const cb of this.listeners) cb();
  }
}

export const mcpAuthStore = new McpAuthStore();

/** Every credential currently awaiting a manual sign-in, at most one per family. */
export function useMcpAuthNotices(): readonly McpAuthNotice[] {
  return useSyncExternalStore(
    mcpAuthStore.subscribe,
    mcpAuthStore.getSnapshot,
    mcpAuthStore.getSnapshot,
  );
}
