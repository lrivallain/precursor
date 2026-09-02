/** Names of the built-in MCP servers that share Precursor's browser OAuth flow. */

/** Human labels for the servers that sign in through the loopback OAuth flow. */
export const OAUTH_SERVER_LABELS: Record<string, string> = {
  workiq: "WorkIQ",
  "workiq-teams": "WorkIQ Teams",
  "workiq-user": "WorkIQ User",
  "workiq-planner": "WorkIQ Planner",
  "workiq-word": "WorkIQ Word",
  "workiq-excel": "WorkIQ Excel",
};

export const OAUTH_SERVERS = new Set(Object.keys(OAUTH_SERVER_LABELS));

/**
 * Which *credential* each OAuth server signs in with — its "auth family".
 *
 * Servers in the same family share one token, one consent and one sign-in, so a
 * single browser round-trip authenticates all of them: the five ``agent365``
 * entries are endpoints of the same Agent 365 client and Entra issues one token
 * good for every one of them. The hosted WorkIQ preview is a *different* Entra
 * client against a different resource, so it is irreducibly its own family and
 * needs its own sign-in.
 *
 * Mirrors the backend's ``WorkIQOAuthProfile.auth_family``: the SPA has to make
 * the same distinction, otherwise it either prompts once per *server* (six
 * prompts for two credentials) or collapses everything into one slot and starves
 * the second family of its hands-free pass.
 */
const OAUTH_AUTH_FAMILIES: Record<string, string> = {
  workiq: "workiq-preview",
  "workiq-teams": "agent365",
  "workiq-user": "agent365",
  "workiq-planner": "agent365",
  "workiq-word": "agent365",
  "workiq-excel": "agent365",
};

/** The credential group ``name`` signs in with (see {@link OAUTH_AUTH_FAMILIES}). */
export function mcpAuthFamily(name: string): string {
  return OAUTH_AUTH_FAMILIES[name] ?? name;
}

/** Display label for an MCP server name, falling back to the raw name. */
export function mcpServerLabel(name: string): string {
  return OAUTH_SERVER_LABELS[name] ?? name;
}
