/** Names of the built-in MCP servers that share Precursor's browser OAuth flow. */

/** Human labels for the servers that sign in through the loopback OAuth flow. */
export const OAUTH_SERVER_LABELS: Record<string, string> = {
  workiq: "WorkIQ",
  "workiq-teams": "WorkIQ Teams",
  "workiq-user": "WorkIQ User",
};

export const OAUTH_SERVERS = new Set(Object.keys(OAUTH_SERVER_LABELS));

/** Display label for an MCP server name, falling back to the raw name. */
export function mcpServerLabel(name: string): string {
  return OAUTH_SERVER_LABELS[name] ?? name;
}
