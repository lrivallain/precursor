import { mcpAuthFamily, mcpServerLabel } from "../../lib/mcpServers";
import type { MCPServerStatus } from "../../lib/types";

/** Apply `patch` to the server called `name`, leaving the others as they are. */
export function patchServer(
  servers: readonly MCPServerStatus[],
  name: string,
  patch: Partial<MCPServerStatus>,
): MCPServerStatus[] {
  return servers.map((s) => (s.name === name ? { ...s, ...patch } : s));
}

/**
 * One sign-in control per *credential*, not per server. The Agent 365
 * endpoints share a single Entra token, so a button on each row invited six
 * sign-ins for the two credentials Precursor actually holds. The first server
 * of each auth family owns the button; its siblings say who they sign in with.
 */
export function signInOwners(servers: readonly MCPServerStatus[]): Map<string, string> {
  const owner = new Map<string, string>();
  for (const server of servers) {
    if (!server.oauth) continue;
    const family = mcpAuthFamily(server.name);
    if (!owner.has(family)) owner.set(family, server.name);
  }
  return owner;
}

/**
 * How a server card offers sign-in: `owner` when it carries the button,
 * `sharedWith` (a label) when a sibling's sign-in covers it.
 */
export function signInRole(
  server: Pick<MCPServerStatus, "name" | "oauth">,
  owners: ReadonlyMap<string, string>,
): { owner: boolean; sharedWith?: string } {
  if (!server.oauth) return { owner: false };
  const owner = owners.get(mcpAuthFamily(server.name)) ?? "";
  return owner === server.name ? { owner: true } : { owner: false, sharedWith: mcpServerLabel(owner) };
}

/** Text colour of a server card's status line. */
export function serverStateColor(state: MCPServerStatus["state"]): string {
  switch (state) {
    case "connected":
    case "ready":
      return "text-green-500";
    case "error":
      return "text-red-500";
    case "needs_auth":
    case "connecting":
      return "text-amber-500";
    default:
      return "text-muted";
  }
}
