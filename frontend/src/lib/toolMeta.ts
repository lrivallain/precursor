import type { WorkspaceFileRef } from "./workspaceLink";

/** Parsed shape of a tool-call message's JSON `tool_calls` payload. */
export interface ParsedToolMeta {
  tool_call_id?: string;
  name?: string;
  arguments?: string;
  is_error?: boolean;
  pending?: boolean;
  /**
   * Set when the tool read or wrote a workspace file. Lifted here by the
   * backend so the UI can offer an "Open" chip without parsing the result body
   * (a read embeds the whole file). See `services/mcp/workspace_links.py`.
   */
  link?: WorkspaceFileRef | null;
}

/** Safely parse a message's `tool_calls` JSON blob, returning null on garbage. */
export function parseToolMeta(raw: string | null): ParsedToolMeta | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as ParsedToolMeta;
    return typeof v === "object" && v !== null ? v : null;
  } catch {
    return null;
  }
}
