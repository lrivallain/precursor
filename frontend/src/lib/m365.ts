import type { Settings } from "./types";

/**
 * Whether Microsoft 365 data is reachable: the WorkIQ MCP server is enabled.
 * Every M365-backed surface (calendar agenda, meeting linking, Teams
 * transcripts) is hidden when it isn't, so a non-M365 install never calls
 * WorkIQ or prompts for a sign-in.
 */
export function m365Enabled(settings: Pick<Settings, "mcp_enabled"> | null): boolean {
  return settings?.mcp_enabled?.workiq ?? false;
}
