import { useSyncExternalStore } from "react";

/**
 * App-global notice that an MCP server needs an interactive sign-in.
 *
 * Background connects (chat turns, workspace runs) never pop a browser — they
 * surface a ``needs_auth`` state and stream an ``mcp_auth_required`` event. Any
 * SSE consumer reports it here so a single banner can offer an inline
 * re-authenticate action from the main app, without a Settings detour.
 */
export interface McpAuthNotice {
  server: string;
  message: string;
}

class McpAuthStore {
  private notice: McpAuthNotice | null = null;
  private listeners = new Set<() => void>();

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  };

  getSnapshot = (): McpAuthNotice | null => this.notice;

  report(server: string, message: string): void {
    const next: McpAuthNotice = { server, message };
    if (
      this.notice &&
      this.notice.server === next.server &&
      this.notice.message === next.message
    ) {
      return;
    }
    this.notice = next;
    this.emit();
  }

  clear(): void {
    if (this.notice === null) return;
    this.notice = null;
    this.emit();
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
