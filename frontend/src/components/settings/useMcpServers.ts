import { useMemo, useState } from "react";
import { api } from "../../lib/api";
import { mcpAuthStore } from "../../lib/mcpAuth";
import type { MCPServerStatus } from "../../lib/types";
import { signInWorkiq } from "../../lib/workiqSignIn";
import { patchServer, signInOwners } from "./mcpServerList";

const errorOf = (err: unknown): string => (err as Error).message;

/**
 * The registered MCP servers and the actions on them. These apply at once
 * (they are not part of the settings Save).
 */
export function useMcpServers() {
  const [servers, setServers] = useState<MCPServerStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<MCPServerStatus | "new" | null>(null);
  const owners = useMemo(() => signInOwners(servers), [servers]);

  const replace = (name: string, next: MCPServerStatus): void =>
    setServers((prev) => prev.map((s) => (s.name === name ? next : s)));
  const patch = (name: string, change: Partial<MCPServerStatus>): void =>
    setServers((prev) => patchServer(prev, name, change));

  async function refresh(): Promise<void> {
    setLoading(true);
    let list: MCPServerStatus[];
    try {
      // Load the list fast, without waiting on per-server status probes so the
      // cards render immediately instead of blocking on a slow server.
      list = await api.mcp.list(false);
      setServers(list);
    } catch {
      setServers([]);
      return;
    } finally {
      setLoading(false);
    }
    // Resolve each pending server's status independently: a slow server only
    // spins its own card rather than stalling the whole list.
    const pending = list.filter((s) => s.enabled && s.state === "connecting");
    await Promise.all(
      pending.map(async (s) => {
        try {
          replace(s.name, await api.mcp.probe(s.name));
        } catch (err) {
          patch(s.name, { state: "error", error: errorOf(err) });
        }
      }),
    );
  }

  async function toggle(name: string, enabled: boolean): Promise<void> {
    // Optimistic update so the switch animates immediately.
    patch(name, { enabled: !enabled, state: enabled ? "disabled" : "connecting" });
    try {
      replace(name, enabled ? await api.mcp.disconnect(name) : await api.mcp.connect(name));
    } catch (err) {
      patch(name, { enabled, state: "error", error: errorOf(err) });
    }
  }

  async function togglePreview(name: string, current: boolean): Promise<void> {
    // Optimistic flip; the round-trip may trigger an OAuth browser sign-in.
    patch(name, { preview: !current });
    try {
      replace(name, await api.mcp.setWorkiqPreview(!current));
    } catch (err) {
      patch(name, { preview: current, state: "error", error: errorOf(err) });
    }
  }

  async function reloadTools(name: string): Promise<void> {
    // Re-probe an enabled server to refresh its tool catalogue in place.
    patch(name, { state: "connecting", error: null });
    try {
      replace(name, await api.mcp.refresh(name));
    } catch (err) {
      patch(name, { state: "error", error: errorOf(err) });
    }
  }

  async function reauthenticate(name: string): Promise<void> {
    // Opens a script-openable sign-in popup and blocks until the user finishes.
    patch(name, { state: "connecting", error: null });
    try {
      const next = await signInWorkiq(name);
      if (!next) {
        // The user abandoned the sign-in (closed the popup) — restore the
        // needs-auth state without an error.
        patch(name, { state: "needs_auth" });
        return;
      }
      replace(name, next);
      // Clear just this credential's notice; the store spends the fresh SSO
      // cookie on a hands-free pass for any sibling that is still stale.
      mcpAuthStore.resolve(name);
      // The backend adopted every server sharing this credential, so re-read the
      // list — otherwise the siblings keep showing the sign-in they just got.
      void refresh();
    } catch (err) {
      patch(name, { state: "needs_auth", error: errorOf(err) });
    }
  }

  async function remove(server: MCPServerStatus): Promise<void> {
    if (server.id == null) return;
    await api.mcp.remove(server.id);
    await refresh();
  }

  return {
    servers, loading, editing, setEditing, owners,
    refresh, toggle, togglePreview, reloadTools, reauthenticate, remove,
  };
}

export type McpServers = ReturnType<typeof useMcpServers>;
